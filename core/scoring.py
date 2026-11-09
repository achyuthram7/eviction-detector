"""Layer 2: rule-based adjustments from the parties' answers, plus every output field
(potential, pathway, confidence, legal-aid flag, 2-4 plain-language reasons).

Pure functions — no database — so the rules are easy to test and to explain. All weights come
from config/scoring.yaml.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from core.constants import (
    AGREED_MOVE_OUT,
    DISMISSAL_ON_PAYMENT,
    HIGH,
    LEGAL_AID,
    LOW,
    MEDIUM,
    NEEDS_OUTREACH,
    NO_RESPONSE,
    PAY_AND_STAY,
)
from core.settings import get_settings


@dataclass
class TenantAnswers:
    believed_owed: float | None = None
    pay_30d: float | None = None
    pay_monthly: float | None = None
    assistance_status: str | None = None
    assistance_amount: float | None = None
    wants: str | None = None
    move_time_days: int | None = None
    repair_issues: bool = False
    retaliation_concern: bool = False
    notice_dispute: bool = False
    payment_dispute: bool = False
    legal_aid: bool | None = None
    willing_mediator: str | None = None


@dataclass
class LandlordAnswers:
    amount_owed_now: float | None = None
    fees_included: float | None = None
    accepts_plan: bool | None = None
    max_plan_months: int | None = None
    min_to_dismiss: float | None = None
    min_to_dismiss_pct: float | None = None
    waive_late_fees: bool | None = None
    waive_attorney_fees: bool | None = None
    extend_moveout: bool | None = None
    extend_moveout_days: int | None = None
    goal: str | None = None
    willing_mediator: str | None = None
    source: str = "token"


@dataclass
class BaseResult:
    """Layer-1 output for one case."""

    prob: float
    contributions: dict[str, float]  # feature -> logit contribution (court dummies summed)
    values: dict[str, float]  # raw feature values
    z: dict[str, float]  # standardized values
    landlord_n: int
    global_rate: float


@dataclass
class ScoreResult:
    base_prob: float
    final_prob: float
    potential: str
    base_tier: str
    pathway: str
    confidence: str
    legal_aid_flag: bool
    overlap_class: str
    reasons: list[str]
    adjustments: dict[str, float] = field(default_factory=dict)
    detail: dict = field(default_factory=dict)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _tier(p: float, t_high: float, t_low: float) -> str:
    return HIGH if p >= t_high else MEDIUM if p >= t_low else LOW


def _pct(x: float) -> str:
    return f"{x:.0%}"


# --- combining several tenants -------------------------------------------------------------------


def _tenant_wants(tenants: list[TenantAnswers]) -> str | None:
    wants = {t.wants for t in tenants if t.wants}
    if not wants:
        return None
    return wants.pop() if len(wants) == 1 else "mixed"


def _willing(values: list[str | None]) -> str | None:
    answered = [v for v in values if v]
    if not answered:
        return None
    if "yes" in answered:
        return "yes"
    if all(v == "no" for v in answered):
        return "no"
    return "unsure"


# --- the rules -----------------------------------------------------------------------------------


def amount_owed(
    landlord: LandlordAnswers | None, tenants: list[TenantAnswers], claimed: float | None
) -> float | None:
    if landlord and landlord.amount_owed_now is not None:
        return landlord.amount_owed_now
    believed = [t.believed_owed for t in tenants if t.believed_owed is not None]
    if believed:
        return max(believed)
    return claimed


def landlord_minimum(landlord: LandlordAnswers | None, owed: float | None) -> float | None:
    if landlord is None:
        return None
    if landlord.min_to_dismiss is not None:
        return landlord.min_to_dismiss
    if landlord.min_to_dismiss_pct is not None and owed is not None:
        return landlord.min_to_dismiss_pct / 100.0 * owed
    return landlord.amount_owed_now


def overlap_check(
    tenants: list[TenantAnswers], landlord: LandlordAnswers | None, owed: float | None
) -> tuple[str, dict]:
    """overlap / bridgeable / wide / unknown, with the numbers behind it."""
    cfg = get_settings().scoring["layer2"]
    minimum = landlord_minimum(landlord, owed)
    money = [t for t in tenants if t.pay_30d is not None or t.pay_monthly is not None]
    if minimum is None or not money or landlord is None:
        return "unknown", {"minimum": minimum}
    months = (landlord.max_plan_months or 0) if landlord.accepts_plan else 0
    capacity = max((t.pay_30d or 0.0) + (t.pay_monthly or 0.0) * months for t in money)
    shortfall = max(0.0, minimum - capacity)
    info = {
        "minimum": round(minimum, 2),
        "capacity": round(capacity, 2),
        "shortfall": round(shortfall, 2),
        "plan_months": months,
    }
    if shortfall == 0:
        return "overlap", info
    assistance = [
        t.assistance_amount
        for t in tenants
        if t.assistance_status in ("applied", "pending", "approved") and t.assistance_amount
    ]
    pending_unknown = any(
        t.assistance_status in ("applied", "pending", "approved") and not t.assistance_amount
        for t in tenants
    )
    waives = bool(landlord.waive_late_fees or landlord.waive_attorney_fees)
    fees = landlord.fees_included
    if fees is None and waives and cfg["assumed_fee_share"]:
        fees = cfg["assumed_fee_share"] * minimum
    if shortfall <= cfg["bridgeable_gap_pct"] * minimum:
        info["bridged_by"] = "small gap"
        return "bridgeable", info
    if assistance and shortfall <= max(assistance):
        info["bridged_by"] = "rental assistance"
        return "bridgeable", info
    if waives and fees and shortfall <= fees:
        info["bridged_by"] = "fee waiver"
        return "bridgeable", info
    if pending_unknown and cfg["pending_unknown_counts_as_bridge"]:
        info["bridged_by"] = "pending assistance (amount unknown)"
        return "bridgeable", info
    return "wide", info


def goals_alignment(tenant_wants: str | None, landlord_goal: str | None) -> str:
    if not tenant_wants or not landlord_goal:
        return "unknown"
    if tenant_wants == "mixed" or landlord_goal == "both":
        return "partial"
    if (tenant_wants, landlord_goal) in (("stay", "rent"), ("move", "possession")):
        return "aligned"
    if (tenant_wants, landlord_goal) == ("stay", "possession"):
        return "conflict"
    return "partial"  # tenant would move, landlord wants rent


def mediation_class(tenant_w: str | None, landlord_w: str | None) -> str:
    if tenant_w == "yes" and landlord_w == "yes":
        return "both_willing"
    if "no" in (tenant_w, landlord_w):
        return "one_refuses"
    if "yes" in (tenant_w, landlord_w):
        return "one_willing"
    return "unknown"


def choose_pathway(
    *,
    tenant_responded: bool,
    landlord_responded: bool,
    legal_issue: bool,
    overlap: str,
    goals: str,
    tenant_wants: str | None,
    tenant_lump_sum: float | None,
    minimum: float | None,
    landlord: LandlordAnswers | None,
) -> str:
    """First matching rule wins (PLAN.md section 7.3)."""
    if not tenant_responded and not landlord_responded:
        return NEEDS_OUTREACH
    if legal_issue and (overlap in ("wide", "unknown") or goals == "conflict"):
        return LEGAL_AID
    goal = landlord.goal if landlord else None
    if tenant_wants == "move" or (goal == "possession" and landlord and landlord.extend_moveout):
        return AGREED_MOVE_OUT
    wants_or_unknown_stay = tenant_wants in ("stay", None, "mixed")
    if (
        tenant_responded
        and wants_or_unknown_stay
        and goal in ("rent", "both")
        and tenant_lump_sum is not None
        and minimum is not None
        and tenant_lump_sum >= minimum
    ):
        return DISMISSAL_ON_PAYMENT
    if (
        tenant_responded
        and wants_or_unknown_stay
        and landlord
        and landlord.accepts_plan
        and overlap in ("overlap", "bridgeable")
    ):
        return PAY_AND_STAY
    if tenant_responded != landlord_responded:  # only one side answered
        if tenant_responded:
            if tenant_wants == "stay":
                return PAY_AND_STAY
            if tenant_wants == "move":
                return AGREED_MOVE_OUT
        elif goal == "possession":
            return AGREED_MOVE_OUT
        elif goal in ("rent", "both"):
            return PAY_AND_STAY
    if legal_issue:
        return LEGAL_AID
    return NEEDS_OUTREACH


# --- reasons -------------------------------------------------------------------------------------


def _num(x) -> float | None:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return float(x)


def _base_reason(name: str, base: BaseResult, templates: dict) -> str | None:
    v, z = base.values, base.z
    if name == "landlord_rate":
        if base.landlord_n < 5:
            return templates["landlord_rate_new"].format(avg=_pct(base.global_rate))
        return templates["landlord_rate"].format(
            rate=_pct(v["landlord_rate"]), avg=_pct(base.global_rate)
        )
    if name == "prior_filings_12m":
        n = int(v["prior_filings_12m"])
        return templates["prior_filings_12m"].format(n=n) if n else templates["prior_filings_none"]
    if name == "plaintiff_attorney":
        key = "plaintiff_attorney_yes" if v["plaintiff_attorney"] else "plaintiff_attorney_no"
        return templates[key]
    if name == "defendant_count":
        return templates["defendant_count"].format(n=int(v["defendant_count"]))
    if name in ("log_amount", "amount_unknown"):
        if v["amount_unknown"]:
            return templates["amount_unknown"]
        log_amount = _num(v.get("log_amount"))
        if log_amount is None:
            return None
        amount = math.expm1(log_amount)
        cmp = "higher" if z.get("log_amount", 0) > 0 else "lower"
        return templates["amount_known"].format(amount=f"{amount:,.0f}", cmp=cmp)
    if name == "court":
        cmp = "more" if base.contributions.get("court", 0) > 0 else "less"
        return templates["court"].format(court=v.get("court_id", "?"), cmp=cmp)
    if name == "days_to_hearing":
        if _num(v.get("days_to_hearing")) is None:
            return None
        cmp = "more" if z.get("days_to_hearing", 0) > 0 else "less"
        return templates["days_to_hearing"].format(n=int(v["days_to_hearing"]), cmp=cmp)
    return None


def _phrase(effect: float, text: str, prefix: str | None = None) -> str:
    lead = prefix or ("Raises" if effect > 0 else "Lowers")
    return f"{lead}: {text}"


def build_reasons(
    base: BaseResult,
    adjustments: dict[str, float],
    detail: dict,
    *,
    limit: int = 4,
) -> list[str]:
    templates = get_settings().scoring["reasons"]
    candidates: list[tuple[float, str]] = []
    # Amount: report the "unknown" indicator and the amount itself as one reason.
    contrib = dict(base.contributions)
    amount_effect = contrib.pop("log_amount", 0.0) + contrib.pop("amount_unknown", 0.0)
    contrib["log_amount"] = amount_effect
    for name, effect in contrib.items():
        text = _base_reason(name, base, templates)
        if text and abs(effect) > 1e-9:
            candidates.append((abs(effect), _phrase(effect, text)))
    for key, weight in adjustments.items():
        if abs(weight) < 1e-9 or key not in detail.get("reason_keys", {}):
            continue
        text = templates[detail["reason_keys"][key]].format(goal=detail.get("goal_text", ""))
        candidates.append((abs(weight), _phrase(weight, text)))
    if detail.get("wide_gap_protected"):
        candidates.append(
            (
                abs(get_settings().scoring["layer2"]["overlap"]["wide"]),
                _phrase(-1, templates["overlap_wide"], prefix="Lowers confidence"),
            )
        )
    candidates.sort(key=lambda c: -c[0])
    reasons = [text for _, text in candidates[:limit]]
    for fallback in ("landlord_rate", "plaintiff_attorney", "defendant_count"):
        if len(reasons) >= 2:
            break
        text = _base_reason(fallback, base, templates)
        effect = base.contributions.get(fallback, 0.0)
        line = _phrase(effect if effect else 1, text) if effect else f"Neutral: {text}"
        if text and line not in reasons:
            reasons.append(line)
    return reasons[:limit]


# --- the main entry point ---------------------------------------------------------------


def score_case(
    base: BaseResult,
    tenants: list[TenantAnswers],
    landlord: LandlordAnswers | None,
    *,
    t_high: float,
    t_low: float,
    amount_claimed: float | None = None,
) -> ScoreResult:
    sc = get_settings().scoring
    l2, conf_cfg = sc["layer2"], sc["confidence"]

    tenant_responded = bool(tenants)
    landlord_responded = landlord is not None
    owed = amount_owed(landlord, tenants, amount_claimed)
    overlap, overlap_info = overlap_check(tenants, landlord, owed)
    t_wants = _tenant_wants(tenants)
    goals = goals_alignment(t_wants, landlord.goal if landlord else None)
    mediation = mediation_class(
        _willing([t.willing_mediator for t in tenants]),
        landlord.willing_mediator if landlord else None,
    )
    legal_issue = any(
        t.repair_issues or t.retaliation_concern or t.notice_dispute or t.payment_dispute
        for t in tenants
    )
    both_willing = mediation == "both_willing"

    adjustments: dict[str, float] = {}
    reason_keys: dict[str, str] = {}
    wide_gap_protected = False
    if overlap != "unknown":
        weight = float(l2["overlap"][overlap])
        if overlap == "wide" and both_willing:
            wide_gap_protected = True  # wide gap lowers confidence only
        else:
            adjustments["overlap"] = weight
            reason_keys["overlap"] = f"overlap_{overlap}"
    if goals != "unknown":
        adjustments["goals"] = float(l2["goals"][goals])
        reason_keys["goals"] = f"goals_{goals}"
    if mediation != "unknown":
        adjustments["mediation"] = float(l2["mediation"][mediation])
        reason_keys["mediation"] = f"mediation_{mediation}"
    if any(t.assistance_status in ("applied", "pending") for t in tenants):
        adjustments["assistance_pending"] = float(l2["assistance_pending"])
        reason_keys["assistance_pending"] = "assistance_pending"
    if landlord and (landlord.waive_late_fees or landlord.waive_attorney_fees):
        adjustments["fee_waiver_offered"] = float(l2["fee_waiver_offered"])
        reason_keys["fee_waiver_offered"] = "fee_waiver_offered"
    if landlord and landlord.extend_moveout and t_wants == "move":
        adjustments["moveout_extension_matches_tenant"] = float(
            l2["moveout_extension_matches_tenant"]
        )
        reason_keys["moveout_extension_matches_tenant"] = "moveout_extension_matches_tenant"
    if legal_issue and l2["legal_issue"]:
        adjustments["legal_issue"] = float(l2["legal_issue"])

    final = _sigmoid(_logit(base.prob) + sum(adjustments.values()))
    floored = False
    if both_willing and l2["both_willing_floor_at_high"] and final < t_high:
        final, floored = t_high, True

    base_tier = _tier(base.prob, t_high, t_low)
    if not tenant_responded and not landlord_responded:
        potential = NO_RESPONSE  # a separate category: never scored as Low
    else:
        potential = _tier(final, t_high, t_low)

    minimum = overlap_info.get("minimum")
    lump = max((t.pay_30d for t in tenants if t.pay_30d is not None), default=None)
    pathway = choose_pathway(
        tenant_responded=tenant_responded,
        landlord_responded=landlord_responded,
        legal_issue=legal_issue,
        overlap=overlap,
        goals=goals,
        tenant_wants=t_wants,
        tenant_lump_sum=lump,
        minimum=minimum,
        landlord=landlord,
    )

    points = 0
    if tenant_responded and landlord_responded:
        points += conf_cfg["both_responded"]
    elif tenant_responded or landlord_responded:
        points += conf_cfg["one_responded"]
    if base.landlord_n >= conf_cfg["landlord_history_min_cases"]:
        points += conf_cfg["landlord_history"]
    points += conf_cfg["overlap"].get(overlap, 0)
    if abs(final - t_high) >= conf_cfg["margin"]:
        points += conf_cfg["margin_points"]
    if t_wants == "mixed":
        points += conf_cfg["tenants_disagree"]
    if overlap_info.get("bridged_by") == "pending assistance (amount unknown)":
        points += conf_cfg["unknown_assistance_bridge"]
    confidence = (
        "High"
        if points >= conf_cfg["high_at"]
        else "Medium"
        if points >= conf_cfg["medium_at"]
        else "Low"
    )

    goal_text = {
        "aligned": {"stay": "tenant stays and rent is paid", "move": "an agreed move-out"}.get(
            t_wants or "", ""
        )
    }.get(goals, "")
    detail = {
        "overlap": overlap_info,
        "goals": goals,
        "mediation": mediation,
        "tenant_wants": t_wants,
        "owed": owed,
        "floored_to_high": floored,
        "wide_gap_protected": wide_gap_protected,
        "reason_keys": reason_keys,
        "goal_text": goal_text,
        "confidence_points": points,
    }
    reasons = build_reasons(base, adjustments, detail)
    return ScoreResult(
        base_prob=base.prob,
        final_prob=final,
        potential=potential,
        base_tier=base_tier,
        pathway=pathway,
        confidence=confidence,
        legal_aid_flag=legal_issue,
        overlap_class=overlap,
        reasons=reasons,
        adjustments=adjustments,
        detail={k: v for k, v in detail.items() if k != "reason_keys"},
    )
