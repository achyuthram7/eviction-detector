"""Intake answers: parsing, saving (resubmission updates, never duplicates), standing policies,
fallback lookups and usability timings.

Visibility rule (hard rule 3): nothing here ever returns one party's answers to the other party.
Public routes only ever load the response that belongs to the token's own party.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import clock
from core.constants import LANDLORD, TENANT, TERMINAL_EVENTS, TERMINAL_STATUSES
from core.models import (
    Case,
    FallbackSubmission,
    IntakeTiming,
    LandlordPolicy,
    LandlordResponse,
    Party,
    Plaintiff,
    TenantResponse,
)
from core.normalize import last_name_norm
from core.parties import ensure_parties, landlord_party, tenant_parties

MAX_MONEY = 100_000.0

TENANT_FIELDS = (
    "believed_owed",
    "pay_30d",
    "pay_monthly",
    "assistance_status",
    "assistance_amount",
    "wants",
    "move_time_days",
    "repair_issues",
    "repair_details",
    "retaliation_concern",
    "notice_dispute",
    "payment_dispute",
    "legal_aid",
    "willing_mediator",
    "consent_text",
    "consent_call",
    "contact_phone",
    "preferred_language",
)
LANDLORD_FIELDS = (
    "amount_owed_now",
    "fees_included",
    "accepts_plan",
    "max_plan_months",
    "min_to_dismiss",
    "waive_late_fees",
    "waive_attorney_fees",
    "extend_moveout",
    "extend_moveout_days",
    "goal",
    "willing_mediator",
)
POLICY_FIELDS = (
    "accepts_plan",
    "max_plan_months",
    "min_to_dismiss_pct",
    "waive_late_fees",
    "waive_attorney_fees",
    "extend_moveout",
    "extend_moveout_days",
    "goal",
    "willing_mediator",
)
# Questions a party must answer before the form is saved. Everything else is labelled
# "optional" on the form. Checkbox groups stay optional: leaving them blank means "no".
# Follow-ups are required only when they apply (moving out, a payment plan, more time).
TENANT_REQUIRED = (
    "believed_owed",
    "pay_30d",
    "pay_monthly",
    "assistance_status",
    "wants",
    "legal_aid",
    "willing_mediator",
    "preferred_language",
)
LANDLORD_REQUIRED = (
    "amount_owed_now",
    "min_to_dismiss",
    "accepts_plan",
    "extend_moveout",
    "goal",
    "willing_mediator",
)
POLICY_REQUIRED = (
    "min_to_dismiss_pct",
    "accepts_plan",
    "extend_moveout",
    "goal",
    "willing_mediator",
)
# Required questions that offer a "Not sure" answer, so a party who doesn't know can still go
# on. "Not sure" is saved as unknown (None), which the scoring already treats as no information.
# The mediator question stores its own "unsure" value; preferred language offers "Other".
TENANT_NOT_SURE = (
    "believed_owed",
    "pay_30d",
    "pay_monthly",
    "assistance_status",
    "wants",
    "move_time_days",
    "legal_aid",
)
LANDLORD_NOT_SURE = (
    "amount_owed_now",
    "min_to_dismiss",
    "accepts_plan",
    "max_plan_months",
    "extend_moveout",
    "extend_moveout_days",
    "goal",
)
POLICY_NOT_SURE = (
    "min_to_dismiss_pct",
    "accepts_plan",
    "max_plan_months",
    "extend_moveout",
    "extend_moveout_days",
    "goal",
)
NOT_SURE = "unsure"
ASSISTANCE = ("none", "applied", "pending", "approved")
WANTS = ("stay", "move")
WILLING = ("yes", "no", "unsure")
GOALS = ("rent", "possession", "both")
LANGUAGES = ("English", "Spanish", "Other")


@dataclass
class FormResult:
    values: dict = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    unsure: set[str] = field(default_factory=set)  # questions answered "Not sure"

    @property
    def ok(self) -> bool:
        return not self.errors


# --- parsing ----------------------------------------------------------------------------------


def _money(form: Mapping, name: str, res: FormResult) -> None:
    raw = (form.get(name) or "").strip().replace("$", "").replace(",", "")
    if not raw:
        res.values[name] = None
        return
    try:
        value = round(float(raw), 2)
    except ValueError:
        res.errors[name] = "err_number"
        return
    if value < 0 or value > MAX_MONEY:
        res.errors[name] = "err_range"
        return
    res.values[name] = value


def _int(form: Mapping, name: str, res: FormResult, lo: int, hi: int) -> None:
    raw = (form.get(name) or "").strip()
    if not raw:
        res.values[name] = None
        return
    try:
        value = int(raw)
    except ValueError:
        res.errors[name] = "err_number"
        return
    if not lo <= value <= hi:
        res.errors[name] = "err_range"
        return
    res.values[name] = value


def _choice(form: Mapping, name: str, res: FormResult, options: tuple[str, ...]) -> None:
    raw = (form.get(name) or "").strip()
    res.values[name] = raw if raw in options else None


def _check(form: Mapping, name: str, res: FormResult) -> None:
    res.values[name] = (form.get(name) or "").strip().lower() in ("on", "1", "true", "yes")


def _yes_no(form: Mapping, name: str, res: FormResult) -> None:
    raw = (form.get(name) or "").strip().lower()
    res.values[name] = True if raw == "yes" else False if raw == "no" else None


def _text(form: Mapping, name: str, res: FormResult, limit: int) -> None:
    raw = (form.get(name) or "").strip()
    res.values[name] = raw[:limit] or None


def _not_sure(form: Mapping, res: FormResult, names: tuple[str, ...]) -> None:
    """Record "Not sure" answers: a choice of "unsure", or a ticked "<name>_unsure" box next to
    an amount. They are saved as unknown (None); a number typed next to a ticked box is ignored."""
    for name in names:
        choice = (form.get(name) or "").strip().lower()
        box = (form.get(f"{name}_unsure") or "").strip().lower()
        if choice == NOT_SURE or box in ("on", "1", "true", "yes"):
            res.values[name] = None
            res.errors.pop(name, None)
            res.unsure.add(name)


def _require(res: FormResult, *names: str) -> None:
    """Flag required questions with no answer. 0, "No" and "Not sure" are all answers."""
    for name in names:
        if name in res.errors or name in res.unsure:
            continue
        if res.values.get(name) is None:
            res.errors[name] = "err_required"


def _landlord_followups(res: FormResult) -> None:
    """Months and days are asked only after a "Yes"; otherwise they are dropped."""
    for gate, follow_up in (
        ("accepts_plan", "max_plan_months"),
        ("extend_moveout", "extend_moveout_days"),
    ):
        if res.values.get(gate) is True:
            _require(res, follow_up)
        else:
            res.values[follow_up] = None
            res.unsure.discard(follow_up)


def unsure_answers(values: dict, kind: str) -> set[str]:
    """Which questions on a saved form were answered "Not sure". New answers can't leave a
    required question blank, so a saved blank there means "Not sure"; the form shows it so."""
    if not values:
        return set()
    allowed = {
        "tenant": TENANT_NOT_SURE,
        "landlord": LANDLORD_NOT_SURE,
        "policy": POLICY_NOT_SURE,
    }[kind]
    gates = {
        "move_time_days": values.get("wants") == "move",
        "max_plan_months": values.get("accepts_plan") is True,
        "extend_moveout_days": values.get("extend_moveout") is True,
    }
    return {name for name in allowed if values.get(name) is None and gates.get(name, True)}


def parse_tenant_form(form: Mapping) -> FormResult:
    res = FormResult()
    for name in ("believed_owed", "pay_30d", "pay_monthly", "assistance_amount"):
        _money(form, name, res)
    _choice(form, "assistance_status", res, ASSISTANCE)
    _choice(form, "wants", res, WANTS)
    _int(form, "move_time_days", res, 1, 365)
    for name in (
        "repair_issues",
        "retaliation_concern",
        "notice_dispute",
        "payment_dispute",
        "consent_text",
        "consent_call",
    ):
        _check(form, name, res)
    _text(form, "repair_details", res, 1000)
    _yes_no(form, "legal_aid", res)
    _choice(form, "willing_mediator", res, WILLING)
    _text(form, "contact_phone", res, 40)
    _choice(form, "preferred_language", res, LANGUAGES)
    _not_sure(form, res, TENANT_NOT_SURE)
    _require(res, *TENANT_REQUIRED)
    if res.values.get("wants") == "move":
        _require(res, "move_time_days")
    else:
        res.values["move_time_days"] = None
        res.unsure.discard("move_time_days")
    return res


def parse_landlord_form(form: Mapping) -> FormResult:
    res = FormResult()
    for name in ("amount_owed_now", "fees_included", "min_to_dismiss"):
        _money(form, name, res)
    _yes_no(form, "accepts_plan", res)
    _int(form, "max_plan_months", res, 1, 24)
    _check(form, "waive_late_fees", res)
    _check(form, "waive_attorney_fees", res)
    _yes_no(form, "extend_moveout", res)
    _int(form, "extend_moveout_days", res, 1, 180)
    _choice(form, "goal", res, GOALS)
    _choice(form, "willing_mediator", res, WILLING)
    _not_sure(form, res, LANDLORD_NOT_SURE)
    _require(res, *LANDLORD_REQUIRED)
    _landlord_followups(res)
    return res


def parse_policy_form(form: Mapping) -> FormResult:
    res = FormResult()
    _int(form, "min_to_dismiss_pct", res, 1, 100)
    if res.values.get("min_to_dismiss_pct") is not None:
        res.values["min_to_dismiss_pct"] = float(res.values["min_to_dismiss_pct"])
    _yes_no(form, "accepts_plan", res)
    _int(form, "max_plan_months", res, 1, 24)
    _check(form, "waive_late_fees", res)
    _check(form, "waive_attorney_fees", res)
    _yes_no(form, "extend_moveout", res)
    _int(form, "extend_moveout_days", res, 1, 180)
    _choice(form, "goal", res, GOALS)
    _choice(form, "willing_mediator", res, WILLING)
    _not_sure(form, res, POLICY_NOT_SURE)
    _require(res, *POLICY_REQUIRED)
    _landlord_followups(res)
    return res


# --- tokens & timings -------------------------------------------------------------------------


def party_for_token(session: Session, token: str | None) -> Party | None:
    if not token or len(token) > 64:
        return None
    party = session.scalar(select(Party).where(Party.token == token))
    if party is None or party.token_revoked_at is not None:
        return None
    return party


def plaintiff_for_token(session: Session, token: str | None) -> Plaintiff | None:
    if not token or len(token) > 64:
        return None
    return session.scalar(select(Plaintiff).where(Plaintiff.portfolio_token == token))


def accepting_answers(case: Case) -> bool:
    return case.working_status not in TERMINAL_STATUSES and case.purged_at is None


def open_timing(
    session: Session,
    *,
    role: str,
    kind: str = "case",
    party: Party | None = None,
    plaintiff_id: str | None = None,
    language: str = "en",
) -> IntakeTiming:
    timing = IntakeTiming(
        party_id=party.id if party else None,
        plaintiff_id=plaintiff_id,
        role=role,
        kind=kind,
        language=language,
        opened_at=clock.now(),
    )
    session.add(timing)
    session.flush()
    return timing


def close_timing(
    session: Session,
    timing_id: str | int | None,
    *,
    party: Party | None = None,
    plaintiff_id: str | None = None,
    kind: str = "case",
) -> IntakeTiming | None:
    """Mark the form-open row as submitted. Only accepts a row that belongs to this party."""
    try:
        timing = session.get(IntakeTiming, int(timing_id)) if timing_id else None
    except (TypeError, ValueError):
        timing = None
    if timing is None or timing.kind != kind:
        return None
    if party is not None and timing.party_id != party.id:
        return None
    if plaintiff_id is not None and timing.plaintiff_id != plaintiff_id:
        return None
    if timing.submitted_at is None:
        timing.submitted_at = clock.now()
    return timing


# --- saving answers ---------------------------------------------------------------------------


def get_tenant_response(session: Session, party: Party) -> TenantResponse | None:
    return session.scalar(select(TenantResponse).where(TenantResponse.party_id == party.id))


def get_landlord_response(session: Session, party: Party) -> LandlordResponse | None:
    return session.scalar(select(LandlordResponse).where(LandlordResponse.party_id == party.id))


def save_tenant_response(
    session: Session,
    party: Party,
    values: dict,
    *,
    started_at=None,
    submitted_at=None,
    source: str = "token",
    verified: bool = True,
    language: str = "en",
) -> TenantResponse:
    """Insert or update the party's single response row (resubmission never duplicates)."""
    assert party.role == TENANT
    now = clock.now()
    resp = get_tenant_response(session, party)
    if resp is not None and source == "fallback" and resp.source == "token":
        return resp  # an unverified lookup never overwrites answers given through the link
    if resp is None:
        resp = TenantResponse(party_id=party.id, case_id=party.case_id, revision=1)
        session.add(resp)
    else:
        resp.revision = (resp.revision or 1) + 1
    for name in TENANT_FIELDS:
        if name in values:
            setattr(resp, name, values[name])
    resp.source = source
    resp.verified = verified
    resp.language = language
    resp.started_at = started_at or now
    resp.submitted_at = submitted_at or now
    resp.updated_at = now
    session.flush()
    return resp


def save_landlord_response(
    session: Session,
    party: Party,
    values: dict,
    *,
    started_at=None,
    submitted_at=None,
    source: str = "token",
    verified: bool = True,
    language: str = "en",
) -> LandlordResponse:
    assert party.role == LANDLORD
    now = clock.now()
    resp = get_landlord_response(session, party)
    if resp is None:
        resp = LandlordResponse(party_id=party.id, case_id=party.case_id, revision=1)
        session.add(resp)
    else:
        if source == "fallback" and resp.source in ("token", "policy"):
            return resp
        resp.revision = (resp.revision or 1) + 1
        if resp.source == "policy" and source != "policy":
            resp.min_to_dismiss_pct = None  # case-specific answers replace the policy
    for name in LANDLORD_FIELDS:
        if name in values:
            setattr(resp, name, values[name])
    resp.source = source
    resp.verified = verified
    resp.language = language
    resp.started_at = started_at or now
    resp.submitted_at = submitted_at or now
    resp.updated_at = now
    session.flush()
    return resp


# --- landlord standing policy -----------------------------------------------------------------


def save_policy(
    session: Session,
    plaintiff: Plaintiff,
    values: dict,
    *,
    started_at=None,
    submitted_at=None,
    language: str = "en",
) -> tuple[LandlordPolicy, list[Case]]:
    """Save the portfolio-level policy and apply it to every open case from this plaintiff."""
    now = clock.now()
    policy = session.scalar(
        select(LandlordPolicy).where(LandlordPolicy.plaintiff_id == plaintiff.plaintiff_id)
    )
    if policy is None:
        policy = LandlordPolicy(plaintiff_id=plaintiff.plaintiff_id, revision=1)
        session.add(policy)
    else:
        policy.revision = (policy.revision or 1) + 1
    for name in POLICY_FIELDS:
        if name in values:
            setattr(policy, name, values[name])
    policy.language = language
    policy.started_at = started_at or now
    policy.submitted_at = submitted_at or now
    policy.updated_at = now
    session.flush()
    affected = []
    open_cases = session.scalars(
        select(Case).where(
            Case.plaintiff_id == plaintiff.plaintiff_id,
            Case.latest_event.not_in(TERMINAL_EVENTS),
        )
    )
    for case in open_cases:
        if apply_policy_to_case(session, case, policy=policy):
            affected.append(case)
    return policy, affected


def apply_policy_to_case(
    session: Session, case: Case, *, policy: LandlordPolicy | None = None
) -> bool:
    """Copy the standing policy onto an open case unless the landlord answered for this case."""
    if case.latest_event in TERMINAL_EVENTS:
        return False
    if policy is None:
        policy = session.scalar(
            select(LandlordPolicy).where(LandlordPolicy.plaintiff_id == case.plaintiff_id)
        )
    if policy is None:
        return False
    party = landlord_party(case)
    if party is None:
        ensure_parties(session, case)
        party = landlord_party(case)
    resp = get_landlord_response(session, party)
    if resp is not None and resp.source != "policy":
        return False
    values = {name: getattr(policy, name) for name in POLICY_FIELDS}
    if resp is not None and all(getattr(resp, k) == v for k, v in values.items()):
        return False
    if resp is None:
        resp = LandlordResponse(party_id=party.id, case_id=case.id, revision=1, source="policy")
        session.add(resp)
    else:
        resp.revision = (resp.revision or 1) + 1
    for name, value in values.items():
        setattr(resp, name, value)
    resp.source = "policy"
    resp.verified = True
    resp.language = policy.language
    resp.started_at = policy.started_at
    resp.submitted_at = policy.submitted_at
    resp.updated_at = clock.now()
    session.flush()
    return True


# --- fallback lookup --------------------------------------------------------------------------


def submit_fallback(
    session: Session,
    *,
    role: str,
    case_number: str,
    last_name: str,
    values: dict,
    language: str = "en",
) -> tuple[FallbackSubmission, Case | None]:
    """Record a case-number + last-name submission.

    The caller must show the same neutral page whatever happens here, so nothing about the
    case leaks. A tenant match attaches the answers as *unverified* (unless the party already
    answered through their link); anything else goes to the staff review queue. Landlord
    fallbacks always go to review (PLAN.md default C1).
    """
    role = role if role in (TENANT, LANDLORD) else TENANT
    number = (case_number or "").strip().upper()[:80]
    sub = FallbackSubmission(
        role_claimed=role,
        case_number_entered=number,
        last_name_entered=(last_name or "").strip()[:120],
        answers={k: v for k, v in values.items()},
        status="review",
        created_at=clock.now(),
    )
    session.add(sub)
    matched_case: Case | None = None
    if role == TENANT and number and sub.last_name_entered:
        case = session.scalar(select(Case).where(Case.case_number == number))
        wanted = last_name_norm(sub.last_name_entered)
        if case is not None and accepting_answers(case) and wanted:
            party = next((p for p in tenant_parties(case) if p.last_name_norm == wanted), None)
            if party is not None:
                sub.matched_party_id = party.id
                sub.status = "matched_unverified"
                if get_tenant_response(session, party) is None:
                    save_tenant_response(
                        session,
                        party,
                        values,
                        source="fallback",
                        verified=False,
                        language=language,
                    )
                    matched_case = case
                else:
                    sub.note = "Party already answered through their link; those answers kept."
    session.flush()
    return sub, matched_case


def attach_fallback(
    session: Session, sub: FallbackSubmission, party: Party, user_id: int | None
) -> None:
    """Staff verified who submitted it: attach the answers to the party as verified."""
    if party.role == TENANT:
        save_tenant_response(session, party, sub.answers, source="fallback", verified=True)
    else:
        save_landlord_response(session, party, sub.answers, source="fallback", verified=True)
    sub.matched_party_id = party.id
    sub.status = "attached"
    sub.reviewed_by = user_id
    sub.reviewed_at = clock.now()


def reject_fallback(
    session: Session, sub: FallbackSubmission, user_id: int | None, note: str | None
) -> None:
    sub.status = "rejected"
    sub.reviewed_by = user_id
    sub.reviewed_at = clock.now()
    sub.note = note
