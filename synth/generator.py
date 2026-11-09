"""Synthetic Indiana-style EV docket generator.

Every person, address and phone number is fake (Faker; phones use the 555-01xx range reserved
for fiction). Court IDs use county numbers that do not exist in Indiana.

The generator simulates each case's full timeline (filing -> service -> hearing -> outcome) from
a latent settlement propensity, so exports can be produced "as of" any date:
  * seeded DB          = export as of anchor - 2 days (history.csv + live.csv)
  * data/demo/day1.csv = export as of anchor - 1 day
  * data/demo/day2.csv = export as of anchor, with a forced continuance, dismissal,
                         diversion stay and newly sealed case
Ground truth (latent probability, true outcome, label-noise flips) is returned separately and
written to its own file; the app never reads it.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
from faker import Faker

from core.constants import (
    AGREED_MOVE_OUT,
    DISMISSAL_ON_PAYMENT,
    PAY_AND_STAY,
    TERMINAL_EVENTS,
)

RESOLVED_CODES = (
    "dismissed_on_payment",
    "dismissed_other",
    "agreed_pay_and_stay",
    "agreed_move_out",
)
UNRESOLVED_CODES = ("default_judgment", "judgment_after_trial")
CODE_EVENT = {
    "dismissed_on_payment": "dismissed",
    "dismissed_other": "dismissed",
    "agreed_pay_and_stay": "agreed_judgment",
    "agreed_move_out": "agreed_judgment",
    "default_judgment": "default_judgment",
    "judgment_after_trial": "judgment_after_trial",
}
CODE_PATHWAY = {
    "dismissed_on_payment": DISMISSAL_ON_PAYMENT,
    "agreed_pay_and_stay": PAY_AND_STAY,
    "agreed_move_out": AGREED_MOVE_OUT,
    "dismissed_other": "",
    "default_judgment": "",
    "judgment_after_trial": "",
}
# Mild tilt of the outcome mix by landlord type (shares are re-normalized).
TYPE_TILT = {
    "payment_seeker": {"dismissed_on_payment": 1.5, "agreed_move_out": 0.5},
    "possession_seeker": {"agreed_move_out": 2.0, "agreed_pay_and_stay": 0.5},
    "mixed": {},
}
HEARING_TIMES = (time(9, 0), time(10, 30), time(13, 30))
AREA_CODES = ("317", "463", "765", "812")
LLC_WORDS = ("Properties", "Rentals", "Housing", "Apartments", "Realty", "Homes", "Management")


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _weekday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


@dataclass
class Household:
    hid: int
    names: list[str]
    address: str
    phones: list[str | None]


@dataclass
class SynthPlaintiff:
    idx: int
    plaintiff_id: str
    name: str
    ptype: str
    effect: float
    attorney_prob: float
    large: bool


@dataclass
class Snapshot:
    event: str
    event_date: date
    hearing: datetime | None
    disposed: bool
    confidential: bool
    sealed: bool


@dataclass
class SynthCase:
    case_number: str
    court_id: str
    filing_date: date
    plaintiff: SynthPlaintiff
    household: Household
    attorney: bool
    amount_owed: float
    amount_claimed: float
    served_date: date
    first_hearing: datetime
    prior_filings: int
    linpred: float
    repeat_of_household: bool = False
    latent_p: float = 0.0
    true_resolved: bool = False
    recorded_resolved: bool = False
    label_flipped: bool = False
    code: str = ""
    disposition_date: date | None = None
    events: list[tuple[date, str]] = field(default_factory=list)
    hearings: list[tuple[date, datetime]] = field(default_factory=list)  # (effective, hearing)
    confidential_date: date | None = None
    sealed_date: date | None = None
    via_diversion: bool = False
    continued: bool = False
    dismissed_before_hearing: bool = False
    sealed_midway: bool = False
    forced: str = ""

    def is_open(self, d: date) -> bool:
        return self.filing_date <= d and (
            self.disposition_date is None or self.disposition_date > d
        )

    def hearing_at(self, d: date) -> datetime | None:
        if d < self.filing_date + timedelta(days=1):
            return None
        current = self.first_hearing
        for eff, h in self.hearings:
            if eff <= d:
                current = h
        return current

    def state_at(self, d: date) -> Snapshot:
        past = [(dt, ev) for dt, ev in self.events if dt <= d]
        event_date, event = past[-1]
        return Snapshot(
            event=event,
            event_date=event_date,
            hearing=self.hearing_at(d),
            disposed=self.disposition_date is not None and self.disposition_date <= d,
            confidential=self.confidential_date is not None and self.confidential_date <= d,
            sealed=self.sealed_date is not None and self.sealed_date <= d,
        )

    def export_row(self, d: date) -> dict:
        s = self.state_at(d)
        hh = self.household
        return {
            "case_number": self.case_number,
            "court_id": self.court_id,
            "filing_date": self.filing_date.isoformat(),
            "case_type": "EV",
            "amount_claimed": f"{self.amount_claimed:.2f}",
            "hearing_datetime": s.hearing.strftime("%Y-%m-%dT%H:%M") if s.hearing else "",
            "plaintiff_id": self.plaintiff.plaintiff_id,
            "plaintiff_attorney": "true" if self.attorney else "false",
            "defendant_count": str(len(hh.names)),
            "defendant_names": "; ".join(hh.names),
            "phone": "; ".join(p or "" for p in hh.phones) if any(hh.phones) else "",
            "mailing_address": hh.address,
            "latest_event": s.event,
            "disposition_type": self.code if s.disposed else "",
            "disposition_date": self.disposition_date.isoformat() if s.disposed else "",
            "confidential_flag": "true" if s.confidential else "false",
            "sealed_flag": "true" if s.sealed else "false",
            "latest_event_date": s.event_date.isoformat(),
            "plaintiff_name": self.plaintiff.name,
            "export_date": d.isoformat(),
        }

    def truth_row(self) -> dict:
        return {
            "case_number": self.case_number,
            "latent_p": round(self.latent_p, 5),
            "true_resolved": int(self.true_resolved),
            "recorded_resolved": int(self.recorded_resolved),
            "label_flipped": int(self.label_flipped),
            "final_disposition_type": self.code,
            "final_disposition_date": self.disposition_date.isoformat()
            if self.disposition_date
            else "",
            "true_pathway": CODE_PATHWAY.get(self.code, ""),
            "via_diversion": int(self.via_diversion),
            "plaintiff_type": self.plaintiff.ptype,
            "forced_demo_change": self.forced,
        }


@dataclass
class World:
    anchor: date
    snapshot_date: date
    alpha: float
    plaintiffs: list[SynthPlaintiff]
    cases: list[SynthCase]
    intakes: list[dict] = field(default_factory=list)
    policies: list[dict] = field(default_factory=list)
    fallbacks: list[dict] = field(default_factory=list)
    call_logs: list[dict] = field(default_factory=list)
    invited: dict[str, datetime] = field(default_factory=dict)
    edge_cases: dict = field(default_factory=dict)
    demo_cases: dict = field(default_factory=dict)
    malformed_day1: list[dict] = field(default_factory=list)
    malformed_day2: list[dict] = field(default_factory=list)

    def by_number(self) -> dict[str, SynthCase]:
        return {c.case_number: c for c in self.cases}

    def export(self, d: date, *, active_since: date | None = None) -> list[dict]:
        """Rows for an export as of date d.

        active_since=None: every case filed by d (closed and open).
        active_since=x:    cases filed by d that were still open after x (a daily active export).
        """
        rows = []
        for c in self.cases:
            if c.filing_date > d:
                continue
            if active_since is not None and not (
                c.disposition_date is None or c.disposition_date > active_since
            ):
                continue
            rows.append(c.export_row(d))
        return rows


# --------------------------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------------------------


def _make_plaintiffs(cfg: dict, rng: np.random.Generator, fake: Faker) -> tuple[list, np.ndarray]:
    pc = cfg["plaintiffs"]
    n = int(pc["count"])
    weights = 1.0 / np.arange(1, n + 1) ** float(pc["zipf_exponent"])
    weights /= weights.sum()
    n_large = max(1, int(round(n * float(pc["large_filer_top_share"]))))
    types = list(pc["types"])
    small_p = np.array([pc["types"][t]["share"] for t in types], dtype=float)
    small_p /= small_p.sum()
    large_p = np.array([pc["large_filer_type_shares"][t] for t in types], dtype=float)
    large_p /= large_p.sum()
    plaintiffs = []
    used_names: set[str] = set()
    for i in range(n):
        large = i < n_large
        ptype = types[int(rng.choice(len(types), p=large_p if large else small_p))]
        effect = float(pc["types"][ptype]["effect"] + rng.normal(0, pc["landlord_effect_sd"]))
        while True:
            name = f"{fake.last_name()} {LLC_WORDS[int(rng.integers(len(LLC_WORDS)))]} LLC"
            if name not in used_names:
                used_names.add(name)
                break
        pid = hashlib.sha256(f"synthetic-plaintiff-{cfg['seed']}-{i}".encode()).hexdigest()[:16]
        plaintiffs.append(
            SynthPlaintiff(
                idx=i,
                plaintiff_id=pid,
                name=name,
                ptype=ptype,
                effect=effect,
                attorney_prob=pc["attorney_prob_large"] if large else pc["attorney_prob_small"],
                large=large,
            )
        )
    return plaintiffs, weights


class Pools:
    """Fake names/streets drawn once from Faker (its weighted sampling is slow per call)."""

    def __init__(self, fake: Faker, n: int = 1500):
        self.first = [fake.first_name() for _ in range(n)]
        self.last = [fake.last_name() for _ in range(n)]
        self.numbers = [fake.building_number() for _ in range(n)]
        self.streets = [fake.street_name() for _ in range(n)]
        self.cities = [fake.city() for _ in range(80)]
        self.zips = [fake.zipcode_in_state("IN") for _ in range(150)]

    @staticmethod
    def pick(rng: np.random.Generator, items: list[str]) -> str:
        return items[int(rng.integers(len(items)))]


def _new_household(hid: int, cfg: dict, rng: np.random.Generator, pools: Pools) -> Household:
    dc = cfg["defendants"]
    counts = [int(k) for k in dc["count_probs"]]
    probs = np.array([dc["count_probs"][k] for k in dc["count_probs"]], dtype=float)
    k = counts[int(rng.choice(len(counts), p=probs / probs.sum()))]
    pick = Pools.pick
    first_last = pick(rng, pools.last)
    names = [f"{pick(rng, pools.first)} {first_last}"]
    for _ in range(k - 1):
        last = first_last if rng.random() < 0.5 else pick(rng, pools.last)
        names.append(f"{pick(rng, pools.first)} {last}")
    unit = f" Apt {int(rng.integers(1, 40))}" if rng.random() < 0.55 else ""
    address = (
        f"{pick(rng, pools.numbers)} {pick(rng, pools.streets)}{unit}, "
        f"{pick(rng, pools.cities)}, IN {pick(rng, pools.zips)}"
    )
    phones: list[str | None] = []
    for i in range(k):
        p = dc["phone_prob_first"] if i == 0 else dc["phone_prob_other"]
        if rng.random() < p:
            area = AREA_CODES[int(rng.integers(len(AREA_CODES)))]
            phones.append(f"{area}-555-01{int(rng.integers(0, 100)):02d}")
        else:
            phones.append(None)
    return Household(hid=hid, names=names, address=address, phones=phones)


def _pick_code(resolved: bool, ptype: str, cfg: dict, rng: np.random.Generator) -> str:
    mix = cfg["outcome_mix"]
    codes = RESOLVED_CODES if resolved else UNRESOLVED_CODES
    tilt = TYPE_TILT.get(ptype, {})
    w = np.array([mix[c] * tilt.get(c, 1.0) for c in codes], dtype=float)
    return codes[int(rng.choice(len(codes), p=w / w.sum()))]


def _timeline(c: SynthCase, cfg: dict, rng: np.random.Generator) -> None:
    """Fill events, hearing changes, disposition date, confidential/sealed dates."""
    f = c.filing_date
    ev: list[tuple[date, str]] = [
        (f, "filed"),
        (f + timedelta(days=1), "hearing_set"),
        (c.served_date, "served"),
    ]
    hearing = c.first_hearing
    hearing_date = hearing.date()
    code = c.code
    disp: date

    if code.startswith("dismissed") and rng.random() < cfg["dismissed_before_hearing_share"]:
        lo = (c.served_date - f).days + 1
        hi = max(lo + 1, (hearing_date - f).days)
        disp = f + timedelta(days=int(rng.integers(lo, hi)))
        ev.append((disp, "dismissed"))
        c.dismissed_before_hearing = True
    else:
        if rng.random() < cfg["continuance_prob"]:
            lo, hi = cfg["timing"]["continuance_days"]
            new_h = datetime.combine(
                _weekday(hearing_date + timedelta(days=int(rng.integers(lo, hi + 1)))),
                hearing.time(),
            )
            ev.append((hearing_date, "diversion_advised"))
            ev.append((hearing_date, "continued"))
            c.hearings.append((hearing_date, new_h))
            hearing_date = new_h.date()
            c.continued = True
        share = (
            cfg["diversion_share_of_resolved"]
            if c.recorded_resolved
            else cfg["diversion_share_of_unresolved"]
        )
        if rng.random() < share:
            c.via_diversion = True
            ev.append((hearing_date, "diversion_advised"))
            ev.append((hearing_date, "diversion_stay"))
            c.confidential_date = hearing_date
            if c.recorded_resolved:
                disp = hearing_date + timedelta(days=int(rng.integers(30, 96)))
                ev.append((disp, CODE_EVENT[code]))
            else:
                fail = hearing_date + timedelta(days=int(rng.integers(30, 61)))
                new_h = datetime.combine(_weekday(fail + timedelta(days=14)), time(9, 0))
                ev.append((fail, "hearing_set"))
                c.hearings.append((fail, new_h))
                disp = new_h.date()
                ev.append((disp, "default_judgment"))
                code = c.code = "default_judgment"
        else:
            if code == "judgment_after_trial":
                disp = hearing_date + timedelta(days=int(rng.integers(0, 22)))
            else:
                disp = hearing_date
            ev.append((disp, CODE_EVENT[code]))

    if code.startswith("dismissed"):
        c.sealed_date = disp  # dismissed cases are sealed automatically
    elif rng.random() < cfg["sealed_midway_prob"] and (disp - f).days > 4:
        c.sealed_date = f + timedelta(days=int(rng.integers(3, (disp - f).days)))
        c.sealed_midway = True
    if code in UNRESOLVED_CODES and rng.random() < cfg["writ_after_possession_prob"]:
        ev.append((disp + timedelta(days=int(rng.integers(7, 15))), "writ_issued"))
    ev.sort(key=lambda x: x[0])  # stable: same-day events keep their order
    c.events = ev
    c.disposition_date = disp


# --------------------------------------------------------------------------------------------
# Main generation
# --------------------------------------------------------------------------------------------


def _generate(cfg: dict, anchor: date, n_recent: int) -> World:
    seed = int(cfg["seed"])
    rng = np.random.default_rng(seed)
    fake = Faker("en_US")
    fake.seed_instance(seed)

    snapshot = anchor - timedelta(days=int(cfg["snapshot_lag_days"]))
    hist_start = snapshot - timedelta(days=365 * int(cfg["history_years"]))
    recent_start = snapshot - timedelta(days=int(cfg["recent_window_days"]))

    plaintiffs, pl_weights = _make_plaintiffs(cfg, rng, fake)
    pools = Pools(fake)
    courts = cfg["courts"]
    court_w = np.array([c["weight"] for c in courts], dtype=float)
    court_w /= court_w.sum()

    hist_days = (recent_start - hist_start).days
    recent_days = (anchor - recent_start).days + 1
    dates = [
        hist_start + timedelta(days=int(o))
        for o in rng.integers(0, hist_days, int(cfg["history_cases"]))
    ]
    dates += [recent_start + timedelta(days=int(o)) for o in rng.integers(0, recent_days, n_recent)]
    # Make sure the two demo days have a few new filings each.
    for day in (anchor - timedelta(days=1), anchor):
        missing = 4 - sum(1 for d in dates if d == day)
        dates += [day] * max(0, missing)
    dates.sort()

    amounts = cfg["amounts"]
    eff = cfg["effects"]
    timing = cfg["timing"]
    log_median = math.log(amounts["owed_median"])

    households: list[Household] = []
    pl_recent: dict[int, deque] = defaultdict(deque)
    hh_filings: dict[int, list[date]] = defaultdict(list)
    counters: dict[tuple[str, int], int] = defaultdict(int)
    cases: list[SynthCase] = []

    for f in dates:
        ci = int(rng.choice(len(courts), p=court_w))
        court = courts[ci]
        pl = plaintiffs[int(rng.choice(len(plaintiffs), p=pl_weights))]

        recent = pl_recent[pl.idx]
        while recent and (f - recent[0][0]).days > 365:
            recent.popleft()
        candidates = [h for d, h in recent if d < f]
        repeat = bool(candidates) and rng.random() < cfg["defendants"]["repeat_prob"]
        if repeat:
            hid = candidates[int(rng.integers(len(candidates)))]
        else:
            hid = len(households)
            households.append(_new_household(hid, cfg, rng, pools))
        hh = households[hid]
        prior = sum(1 for d in hh_filings[hid] if 0 < (f - d).days <= 365)
        hh_filings[hid].append(f)
        recent.append((f, hid))

        attorney = bool(rng.random() < pl.attorney_prob)
        owed = float(
            np.clip(
                rng.lognormal(log_median, amounts["owed_sigma"]),
                amounts["owed_min"],
                amounts["owed_max"],
            )
        )
        owed = round(owed, 2)
        if rng.random() < amounts["placeholder_cap_share"]:
            claimed = float(amounts["claim_cap"])
        else:
            claimed = round(owed * (1 + rng.uniform(0, amounts["fee_markup_max"])), 2)

        s_lo, s_hi = timing["service_days"]
        served = f + timedelta(days=int(rng.integers(s_lo, s_hi + 1)))
        h_date = _weekday(
            served
            + timedelta(
                days=int(timing["hearing_after_service_days"])
                + int(court["hearing_offset_days"])
                + int(rng.integers(-2, 3))
            )
        )
        first_hearing = datetime.combine(
            h_date, HEARING_TIMES[int(rng.integers(len(HEARING_TIMES)))]
        )
        dth = (h_date - f).days

        linpred = (
            pl.effect
            + float(court["effect"])
            + eff["attorney"] * attorney
            + eff["per_extra_defendant"] * (len(hh.names) - 1)
            + eff["log_amount"] * (math.log(owed) - log_median)
            + eff["days_to_hearing_per_day"] * (dth - 28)
            + eff["prior_filing"] * min(prior, 3)
            + float(rng.normal(0, eff["noise_sd"]))
        )

        key = (court["id"], f.year)
        counters[key] += 1
        number = f"{court['id']}-{f:%y%m}-EV-{counters[key]:06d}"
        cases.append(
            SynthCase(
                case_number=number,
                court_id=court["id"],
                filing_date=f,
                plaintiff=pl,
                household=hh,
                attorney=attorney,
                amount_owed=owed,
                amount_claimed=claimed,
                served_date=served,
                first_hearing=first_hearing,
                prior_filings=prior,
                linpred=linpred,
                repeat_of_household=repeat,
            )
        )

    # Calibrate the intercept so the overall resolution share matches the configured mix.
    target = sum(cfg["outcome_mix"][c] for c in RESOLVED_CODES) / sum(cfg["outcome_mix"].values())
    lin = np.array([c.linpred for c in cases])
    lo, hi = -8.0, 8.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if (1 / (1 + np.exp(-(mid + lin)))).mean() < target:
            lo = mid
        else:
            hi = mid
    alpha = (lo + hi) / 2

    for c in cases:
        c.latent_p = _sigmoid(alpha + c.linpred)
        c.true_resolved = bool(rng.random() < c.latent_p)
        c.label_flipped = bool(rng.random() < cfg["label_noise"])
        c.recorded_resolved = c.true_resolved ^ c.label_flipped
        c.code = _pick_code(c.recorded_resolved, c.plaintiff.ptype, cfg, rng)
        _timeline(c, cfg, rng)

    return World(
        anchor=anchor, snapshot_date=snapshot, alpha=alpha, plaintiffs=plaintiffs, cases=cases
    )


def build_world(cfg: dict, anchor: date) -> World:
    """Generate the world, sizing the recent window so ~live_cases are open at the snapshot."""
    guess = int(cfg["live_cases"]) * 2
    world = _generate(cfg, anchor, guess)
    s = world.snapshot_date
    recent_start = s - timedelta(days=int(cfg["recent_window_days"]))
    open_old = sum(1 for c in world.cases if c.is_open(s) and c.filing_date < recent_start)
    open_recent = sum(1 for c in world.cases if c.is_open(s) and c.filing_date >= recent_start)
    wanted = max(int(cfg["live_cases"]) - open_old, 10)
    n_recent = max(20, int(round(guess * wanted / max(open_recent, 1))))
    world = _generate(cfg, anchor, n_recent)
    rng = np.random.default_rng(int(cfg["seed"]) + 1)
    fake = Faker("en_US")
    fake.seed_instance(int(cfg["seed"]) + 1)
    _add_intakes(world, cfg, rng, fake)
    _force_demo_changes(world, rng)
    _mark_edge_cases(world, rng, fake)
    _malformed_rows(world)
    return world


# --------------------------------------------------------------------------------------------
# Intake answers (live docket subset)
# --------------------------------------------------------------------------------------------


def _choice(rng: np.random.Generator, options: list, probs: list[float]):
    p = np.clip(np.array(probs, dtype=float), 0.001, None)
    return options[int(rng.choice(len(options), p=p / p.sum()))]


def tenant_answers(c: SynthCase, rng: np.random.Generator, spanish_share: float) -> dict:
    owed = c.amount_owed
    lean = c.latent_p - 0.5
    type_shift = {"payment_seeker": 0.1, "possession_seeker": -0.1}.get(c.plaintiff.ptype, 0.0)
    wants = "stay" if rng.random() < 0.55 + 0.3 * lean + type_shift else "move"
    pay_30 = max(0.0, owed * float(np.clip(rng.normal(0.35 + 0.5 * lean, 0.2), 0, 1.2)))
    monthly = owed * float(np.clip(rng.normal(0.22 + 0.2 * lean, 0.08), 0.02, 0.8))
    assistance = _choice(rng, ["none", "applied", "pending", "approved"], [0.55, 0.25, 0.15, 0.05])
    amount = None
    if assistance in ("pending", "approved") and rng.random() < 0.6:
        amount = float(round(rng.uniform(500, 3000), -1))
    repair = bool(rng.random() < 0.18)
    lang = "es" if rng.random() < spanish_share else "en"
    return {
        "believed_owed": float(round(owed * rng.uniform(0.85, 1.05), -1)),
        "pay_30d": float(round(pay_30, -1)),
        "pay_monthly": float(round(monthly, -1)),
        "assistance_status": assistance,
        "assistance_amount": amount,
        "wants": wants,
        "move_time_days": int(_choice(rng, [14, 30, 45, 60], [0.2, 0.4, 0.2, 0.2]))
        if wants == "move"
        else None,
        "repair_issues": repair,
        "repair_details": "Heat and plumbing problems reported to the landlord (synthetic)."
        if repair
        else None,
        "retaliation_concern": bool(repair and rng.random() < 0.25),
        "notice_dispute": bool(rng.random() < 0.08),
        "payment_dispute": bool(rng.random() < 0.10),
        "legal_aid": bool(rng.random() < 0.12),
        "willing_mediator": _choice(
            rng, ["yes", "no", "unsure"], [0.6 + 0.3 * lean, 0.12 - 0.1 * lean, 0.28]
        ),
        "consent_text": bool(rng.random() < 0.7),
        "consent_call": bool(rng.random() < 0.6),
        "preferred_language": "Spanish" if lang == "es" else "English",
        "language": lang,
    }


def landlord_answers(c: SynthCase, rng: np.random.Generator) -> dict:
    owed = c.amount_owed
    ptype = c.plaintiff.ptype
    lean = c.latent_p - 0.5
    fees = round(owed * rng.uniform(0, 0.12), 2)
    accepts = bool(
        rng.random() < {"payment_seeker": 0.85, "possession_seeker": 0.15}.get(ptype, 0.55)
    )
    min_share = rng.uniform(0.7, 1.0) if ptype == "payment_seeker" else rng.uniform(0.85, 1.0)
    extend = bool(rng.random() < {"possession_seeker": 0.6, "payment_seeker": 0.3}.get(ptype, 0.4))
    goal_probs = {
        "payment_seeker": [0.7, 0.0, 0.3],
        "possession_seeker": [0.0, 0.7, 0.3],
    }.get(ptype, [0.35, 0.25, 0.4])
    willing_yes = {"payment_seeker": 0.7, "possession_seeker": 0.4}.get(ptype, 0.6) + 0.2 * lean
    return {
        "amount_owed_now": round(owed + fees, 2),
        "fees_included": fees,
        "accepts_plan": accepts,
        "max_plan_months": int(rng.integers(2, 7)) if accepts else None,
        "min_to_dismiss": float(round(owed * min_share, -1)),
        "waive_late_fees": bool(rng.random() < 0.45),
        "waive_attorney_fees": bool(rng.random() < 0.25),
        "extend_moveout": extend,
        "extend_moveout_days": int(_choice(rng, [14, 21, 30, 45], [0.3, 0.3, 0.3, 0.1]))
        if extend
        else None,
        "goal": _choice(rng, ["rent", "possession", "both"], goal_probs),
        "willing_mediator": _choice(
            rng, ["yes", "no", "unsure"], [willing_yes, 0.2 - 0.1 * lean, 0.2]
        ),
        "language": "en",
    }


def policy_answers(p: SynthPlaintiff, rng: np.random.Generator) -> dict:
    accepts = p.ptype != "possession_seeker" or rng.random() < 0.3
    return {
        "accepts_plan": bool(accepts),
        "max_plan_months": int(rng.integers(3, 7)) if accepts else None,
        "min_to_dismiss_pct": float(round(rng.uniform(80, 100), 0)),
        "waive_late_fees": bool(rng.random() < 0.5),
        "waive_attorney_fees": bool(rng.random() < 0.3),
        "extend_moveout": bool(rng.random() < 0.5),
        "extend_moveout_days": 21,
        "goal": {"payment_seeker": "rent", "possession_seeker": "possession"}.get(p.ptype, "both"),
        "willing_mediator": "yes" if rng.random() < 0.75 else "unsure",
        "language": "en",
    }


def _duration(rng: np.random.Generator, median: float, sigma: float) -> timedelta:
    return timedelta(seconds=int(np.clip(rng.lognormal(math.log(median), sigma), 45, 1800)))


def _add_intakes(world: World, cfg: dict, rng: np.random.Generator, fake: Faker) -> None:
    ic = cfg["intake"]
    s = world.snapshot_date
    open_cases = sorted((c for c in world.cases if c.is_open(s)), key=lambda c: c.case_number)

    for p in world.plaintiffs:
        if p.large and rng.random() < ic["policy_share_large_filers"]:
            opened = datetime.combine(s - timedelta(days=int(rng.integers(5, 40))), time(11, 0))
            world.policies.append(
                {
                    "plaintiff_id": p.plaintiff_id,
                    "answers": policy_answers(p, rng),
                    "opened_at": opened,
                    "submitted_at": opened
                    + _duration(rng, ic["landlord_seconds_median"], ic["seconds_sigma"]),
                }
            )
    policy_ids = {pol["plaintiff_id"] for pol in world.policies}

    def stamp(invited: datetime, median: float) -> tuple[datetime, datetime]:
        latest = datetime.combine(s, time(18, 0))
        span = max((latest - invited).total_seconds(), 3600)
        opened = invited + timedelta(seconds=float(rng.uniform(0.1, 1.0)) * span)
        opened = min(opened.replace(microsecond=0), latest - timedelta(hours=1))
        return opened, opened + _duration(rng, median, ic["seconds_sigma"])

    for c in open_cases:
        if (s - c.filing_date).days < 3 or rng.random() > ic["invited_share"]:
            continue
        invited = datetime.combine(
            min(c.filing_date + timedelta(days=int(rng.integers(1, 4))), s), time(10, 0)
        )
        world.invited[c.case_number] = invited
        engaged = 0.7 + 0.6 * c.latent_p
        for idx in range(1, len(c.household.names) + 1):
            rate = ic["tenant_response_rate"] * engaged
            if idx > 1:
                rate *= ic["extra_tenant_response_rate"]
            if rng.random() < rate:
                opened, submitted = stamp(invited, ic["tenant_seconds_median"])
                answers = tenant_answers(c, rng, ic["spanish_share"])
                world.intakes.append(
                    {
                        "case_number": c.case_number,
                        "role": "tenant",
                        "party_index": idx,
                        "answers": answers,
                        "timings": [(opened, submitted)],
                        "revision": 1,
                    }
                )
        if rng.random() < ic["landlord_response_rate"] * (
            0.5 if c.plaintiff.plaintiff_id in policy_ids else 1.0
        ):
            opened, submitted = stamp(invited, ic["landlord_seconds_median"])
            world.intakes.append(
                {
                    "case_number": c.case_number,
                    "role": "landlord",
                    "party_index": 0,
                    "answers": landlord_answers(c, rng),
                    "timings": [(opened, submitted)],
                    "revision": 1,
                }
            )
        if rng.random() < ic["call_log_share"]:
            at = invited + timedelta(days=float(rng.uniform(0.5, 3)))
            at = min(at, datetime.combine(s, time(16, 0))).replace(microsecond=0)
            outcome = _choice(
                rng,
                ["reached", "voicemail", "no_answer", "wrong_number"],
                [0.35, 0.35, 0.25, 0.05],
            )
            world.call_logs.append(
                {
                    "case_number": c.case_number,
                    "at": at,
                    "channel": "phone",
                    "outcome": outcome,
                    "notes": "Explained diversion and the intake link (synthetic note).",
                    "next_action": "Call again before the hearing"
                    if outcome != "reached"
                    else None,
                }
            )


# --------------------------------------------------------------------------------------------
# Demo changes for day2.csv, edge cases, malformed rows
# --------------------------------------------------------------------------------------------


def _replace_future(c: SynthCase, day: date) -> None:
    c.events = [(d, e) for d, e in c.events if d < day]
    c.hearings = [(eff, h) for eff, h in c.hearings if eff < day]
    if c.confidential_date and c.confidential_date >= day:
        c.confidential_date = None
    if c.sealed_date and c.sealed_date >= day:
        c.sealed_date = None


def _force_continuance(c: SynthCase, day: date) -> None:
    shift = timedelta(days=21)
    current = c.hearing_at(day - timedelta(days=1))
    assert current is not None
    c.events = [(d if d < day else d + shift, e) for d, e in c.events]
    c.events.append((day, "continued"))
    c.events.sort(key=lambda x: x[0])
    c.hearings = [(eff, h) if eff < day else (eff + shift, h + shift) for eff, h in c.hearings]
    new_h = datetime.combine(_weekday((current + shift).date()), current.time())
    c.hearings.append((day, new_h))
    c.hearings.sort(key=lambda x: x[0])
    if c.disposition_date:
        c.disposition_date += shift
    if c.confidential_date and c.confidential_date >= day:
        c.confidential_date += shift
    if c.sealed_date and c.sealed_date >= day:
        c.sealed_date += shift
    c.continued = True
    c.forced = "continuance"


def _force_dismissal(c: SynthCase, day: date) -> None:
    _replace_future(c, day)
    c.events.append((day, "dismissed"))
    c.code = "dismissed_on_payment"
    c.disposition_date = day
    c.sealed_date = day
    c.true_resolved = c.recorded_resolved = True
    c.label_flipped = False
    c.dismissed_before_hearing = True
    c.forced = "dismissal"


def _force_diversion(c: SynthCase, day: date) -> None:
    _replace_future(c, day)
    c.events += [(day, "diversion_advised"), (day, "diversion_stay")]
    c.confidential_date = day
    c.via_diversion = True
    if c.recorded_resolved:
        c.disposition_date = day + timedelta(days=60)
        c.events.append((c.disposition_date, CODE_EVENT[c.code]))
        if c.code.startswith("dismissed"):
            c.sealed_date = c.disposition_date
    else:
        fail = day + timedelta(days=45)
        new_h = datetime.combine(_weekday(fail + timedelta(days=14)), time(9, 0))
        c.events.append((fail, "hearing_set"))
        c.hearings.append((fail, new_h))
        c.code = "default_judgment"
        c.disposition_date = new_h.date()
        c.events.append((c.disposition_date, "default_judgment"))
    c.forced = "diversion_stay"


def _force_seal(c: SynthCase, day: date) -> None:
    c.sealed_date = day
    c.sealed_midway = True
    c.forced = "sealed"


def _force_demo_changes(world: World, rng: np.random.Generator) -> None:
    t = world.anchor
    y = t - timedelta(days=1)
    s = world.snapshot_date
    intake_cases = {i["case_number"] for i in world.intakes}
    tenant_intake = {i["case_number"] for i in world.intakes if i["role"] == "tenant"}

    def quiet(c: SynthCase) -> bool:
        h = c.hearing_at(y)
        return (
            c.filing_date <= s
            and c.is_open(t + timedelta(days=3))
            and h is not None
            and h.date() >= t + timedelta(days=2)
            and not any(d in (y, t) for d, _ in c.events)
            and (c.sealed_date is None or c.sealed_date > t + timedelta(days=3))
            and (c.confidential_date is None or c.confidential_date > t + timedelta(days=3))
        )

    pool = sorted((c for c in world.cases if quiet(c)), key=lambda c: c.case_number)
    order = list(rng.permutation(len(pool)))
    picked: dict[str, SynthCase] = {}

    def take(pred) -> SynthCase:
        for i in order:
            c = pool[i]
            if c.case_number in {p.case_number for p in picked.values()}:
                continue
            if pred(c):
                return c
        for i in order:  # fall back to any quiet case
            c = pool[i]
            if c.case_number not in {p.case_number for p in picked.values()}:
                return c
        raise RuntimeError("not enough open cases to build the day-2 demo changes")

    picked["continuance"] = take(lambda c: (c.hearing_at(y).date() - t).days <= 10)
    picked["dismissal"] = take(lambda c: c.case_number not in intake_cases)
    picked["diversion_stay"] = take(lambda c: c.case_number in tenant_intake)
    picked["newly_sealed"] = take(lambda c: c.case_number in tenant_intake)
    _force_continuance(picked["continuance"], t)
    _force_dismissal(picked["dismissal"], t)
    _force_diversion(picked["diversion_stay"], t)
    _force_seal(picked["newly_sealed"], t)

    by_case_roles: dict[str, set] = defaultdict(set)
    for i in world.intakes:
        by_case_roles[i["case_number"]].add(i["role"])
    policy_ids = {p["plaintiff_id"] for p in world.policies}
    forced = {c.case_number for c in picked.values()}
    agreement = next(
        (
            c
            for c in pool
            if c.case_number not in forced
            and "tenant" in by_case_roles[c.case_number]
            and (
                "landlord" in by_case_roles[c.case_number] or c.plaintiff.plaintiff_id in policy_ids
            )
        ),
        None,
    )
    new_y = sorted((c for c in world.cases if c.filing_date == y), key=lambda c: c.case_number)
    world.demo_cases = {
        "anchor_date": t.isoformat(),
        "seed_snapshot_date": s.isoformat(),
        "day1_export_date": y.isoformat(),
        "day2_export_date": t.isoformat(),
        "day1_new_filing_for_qr_demo": new_y[0].case_number if new_y else None,
        "day2_continuance": picked["continuance"].case_number,
        "day2_dismissal": picked["dismissal"].case_number,
        "day2_diversion_stay": picked["diversion_stay"].case_number,
        "day2_newly_sealed": picked["newly_sealed"].case_number,
        "diversion_agreement_candidate": agreement.case_number if agreement else None,
    }


def _mark_edge_cases(world: World, rng: np.random.Generator, fake: Faker) -> None:
    s = world.snapshot_date
    edges: dict = {}
    forced = {v for k, v in world.demo_cases.items() if k.startswith("day2_")}

    # Multiple tenants on one case who answer differently.
    tenant_intakes: dict[str, list[dict]] = defaultdict(list)
    for i in world.intakes:
        if i["role"] == "tenant":
            tenant_intakes[i["case_number"]].append(i)
    multi = next(
        (
            c
            for c in sorted(world.cases, key=lambda c: c.case_number)
            if c.is_open(s)
            and len(c.household.names) >= 2
            and c.case_number in world.invited
            and c.case_number not in forced
        ),
        None,
    )
    if multi is not None:
        existing = {i["party_index"]: i for i in tenant_intakes[multi.case_number]}
        for idx, wants in ((1, "stay"), (2, "move")):
            if idx not in existing:
                invited = world.invited[multi.case_number]
                opened = invited + timedelta(hours=20 + idx)
                entry = {
                    "case_number": multi.case_number,
                    "role": "tenant",
                    "party_index": idx,
                    "answers": tenant_answers(multi, rng, 0.0),
                    "timings": [(opened, opened + timedelta(seconds=200 + 40 * idx))],
                    "revision": 1,
                }
                world.intakes.append(entry)
                existing[idx] = entry
            existing[idx]["answers"]["wants"] = wants
            existing[idx]["answers"]["move_time_days"] = 30 if wants == "move" else None
        edges["multiple_tenants_answer_differently"] = multi.case_number

    # Reused token: one tenant intake submitted twice (second submit updated the first).
    reuse = next(
        (
            i
            for i in world.intakes
            if i["role"] == "tenant"
            and i["case_number"] != edges.get("multiple_tenants_answer_differently")
        ),
        None,
    )
    if reuse is not None:
        first_open, first_submit = reuse["timings"][0]
        earlier = first_open - timedelta(hours=26)
        reuse["timings"].insert(0, (earlier, earlier + timedelta(seconds=240)))
        reuse["revision"] = 2
        edges["reused_token"] = {
            "case_number": reuse["case_number"],
            "party_index": reuse["party_index"],
        }

    hist = sorted(
        (c for c in world.cases if not c.is_open(s) and c.filing_date <= s),
        key=lambda c: c.case_number,
    )
    edges["continuance_history"] = next((c.case_number for c in hist if c.continued), None)
    edges["dismissed_before_hearing"] = next(
        (c.case_number for c in hist if c.dismissed_before_hearing), None
    )
    edges["sealed_midway"] = next((c.case_number for c in hist if c.sealed_midway), None)

    # Refiled case: same landlord + household filed again after an earlier dismissal.
    last_case: dict[tuple[str, int], SynthCase] = {}
    for c in sorted(world.cases, key=lambda c: (c.filing_date, c.case_number)):
        key = (c.plaintiff.plaintiff_id, c.household.hid)
        prev = last_case.get(key)
        if (
            prev is not None
            and prev.code.startswith("dismissed")
            and prev.disposition_date
            and prev.disposition_date < c.filing_date
            and "refiled" not in edges
        ):
            edges["refiled"] = {"original": prev.case_number, "refiled": c.case_number}
        last_case[key] = c

    # Unmatched fallback: a valid case number entered with a last name that is not a party.
    target = next(c for c in sorted(world.cases, key=lambda c: c.case_number) if c.is_open(s))
    party_lasts = {n.split()[-1].lower() for n in target.household.names}
    while True:
        wrong = fake.last_name()
        if wrong.lower() not in party_lasts:
            break
    when = datetime.combine(s, time(9, 30))
    world.fallbacks.append(
        {
            "role_claimed": "tenant",
            "case_number_entered": target.case_number,
            "last_name_entered": wrong,
            "answers": {"wants": "stay", "willing_mediator": "yes", "pay_30d": 300.0},
            "created_at": when,
        }
    )
    edges["unmatched_fallback"] = {"case_number": target.case_number, "last_name": wrong}
    world.edge_cases = {k: v for k, v in edges.items() if v is not None}


def _malformed_rows(world: World) -> None:
    """Rows that the importer must reject (shown in the demo import report)."""
    y = world.anchor - timedelta(days=1)
    template = next(c for c in world.cases if c.filing_date == y).export_row(y)
    bad_pattern = dict(template, case_number="97K01-" + f"{y:%y%m}" + "-EV-12345")
    wrong_type = dict(
        template,
        case_number=f"98K01-{y:%y%m}-EV-9{template['case_number'][-5:]}",
        case_type="SC",
    )
    bad_count = dict(
        template,
        case_number=f"99K01-{y:%y%m}-EV-8{template['case_number'][-5:]}",
        defendant_count=str(int(template["defendant_count"]) + 1),
    )
    world.malformed_day1 = [bad_pattern, wrong_type, bad_count]
    world.malformed_day2 = [
        dict(
            template,
            case_number=f"97K02-{y:%y%m}-EV-7{template['case_number'][-5:]}",
            filing_date="",
        )
    ]


def is_terminal_event(event: str) -> bool:
    return event in TERMINAL_EVENTS
