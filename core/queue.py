"""Staff call queue: which cases to contact, in what order, with what next step.

Restricted cases are filtered out here (the query layer) for clerks and judges, not just in
templates.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from core import clock
from core.constants import (
    BOTH,
    HIGH,
    IN_DIVERSION,
    INVITED,
    LOW,
    MEDIUM,
    NEW,
    NO_RESPONSE,
    PARTIAL,
    REFERRED,
    SEES_RESTRICTED,
    TENANT,
    TERMINAL_STATUSES,
)
from core.models import CallLog, Case, CaseScore, LandlordResponse, TenantResponse
from core.status import diversion_dates

POTENTIAL_ORDER = {HIGH: 0, NO_RESPONSE: 1, MEDIUM: 2, LOW: 3}


@dataclass
class QueueRow:
    case: Case
    days_to_hearing: int | None
    tenant_status: str
    landlord_status: str
    tenant_responded: bool
    landlord_responded: bool
    potential: str | None
    pathway: str | None
    confidence: str | None
    base_tier: str | None
    final_prob: float | None
    legal_aid_flag: bool
    consent: str
    last_contact: CallLog | None
    next_action: str
    diversion: dict | None


def can_see_restricted(role: str) -> bool:
    return role in SEES_RESTRICTED


def visible_case_query(role: str):
    q = select(Case)
    if not can_see_restricted(role):
        q = q.where(Case.is_restricted.is_(False))
    return q


def get_visible_case(session: Session, case_id: int, role: str) -> Case | None:
    case = session.get(Case, case_id)
    if case is None or (case.is_restricted and not can_see_restricted(role)):
        return None
    return case


def _consent_label(responses: list[TenantResponse]) -> str:
    if not responses:
        return "—"
    parts = []
    if any(r.consent_text for r in responses):
        parts.append("Text")
    if any(r.consent_call for r in responses):
        parts.append("Call")
    return " + ".join(parts) if parts else "No"


def suggest_next_action(row: QueueRow, today: date) -> str:
    status = row.case.working_status
    hearing_soon = row.days_to_hearing is not None and row.days_to_hearing <= 7
    action: str
    if status == IN_DIVERSION and row.diversion:
        nxt = (
            row.diversion["check_30"]
            if row.diversion["check_30"] >= today
            else row.diversion["check_60"]
        )
        action = f"Status check on {nxt:%b %d}"
    elif status == NEW:
        action = "Send invitation letters (QR links)"
    elif status == REFERRED:
        action = "Facilitator: schedule mediation"
    elif status == BOTH:
        action = "Refer to facilitator" if row.potential in (HIGH, MEDIUM) else "Review answers"
    elif status == PARTIAL:
        action = "Contact landlord" if row.tenant_responded else "Contact tenant"
    elif status == INVITED:
        action = "Call tenant (no response yet)" if hearing_soon else "Follow up on invitation"
    else:
        action = "Review case"
    if row.legal_aid_flag:
        action += " · offer legal-aid referral"
    return action


def build_rows(session: Session, cases: list[Case]) -> list[QueueRow]:
    ids = [c.id for c in cases]
    tenant_resp: dict[int, list[TenantResponse]] = defaultdict(list)
    landlord_resp: dict[int, LandlordResponse] = {}
    scores: dict[int, CaseScore] = {}
    calls: dict[int, CallLog] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        for r in session.scalars(select(TenantResponse).where(TenantResponse.case_id.in_(chunk))):
            tenant_resp[r.case_id].append(r)
        for r in session.scalars(
            select(LandlordResponse).where(LandlordResponse.case_id.in_(chunk))
        ):
            landlord_resp[r.case_id] = r
        for s in session.scalars(
            select(CaseScore).where(CaseScore.case_id.in_(chunk), CaseScore.is_current.is_(True))
        ):
            scores[s.case_id] = s
        for c in session.scalars(
            select(CallLog).where(CallLog.case_id.in_(chunk)).order_by(CallLog.at)
        ):
            calls[c.case_id] = c  # last one wins (ordered by time)

    today = clock.today()
    rows: list[QueueRow] = []
    for case in cases:
        n_tenants = sum(1 for p in case.parties if p.role == TENANT) or case.defendant_count
        t_list = tenant_resp.get(case.id, [])
        if not t_list:
            t_label = "No"
        elif len(t_list) >= n_tenants:
            t_label = "Yes" if n_tenants == 1 else f"Yes ({len(t_list)}/{n_tenants})"
        else:
            t_label = f"Partial ({len(t_list)}/{n_tenants})"
        if any(not r.verified for r in t_list):
            t_label += " · unverified"
        lr = landlord_resp.get(case.id)
        l_label = "No" if lr is None else ("Policy" if lr.source == "policy" else "Yes")
        if lr is not None and not lr.verified:
            l_label += " · unverified"
        score = scores.get(case.id)
        dth = (case.hearing_datetime.date() - today).days if case.hearing_datetime else None
        last = calls.get(case.id)
        row = QueueRow(
            case=case,
            days_to_hearing=dth,
            tenant_status=t_label,
            landlord_status=l_label,
            tenant_responded=bool(t_list),
            landlord_responded=lr is not None,
            potential=score.potential if score else None,
            pathway=score.pathway if score else None,
            confidence=score.confidence if score else None,
            base_tier=score.base_tier if score else None,
            final_prob=score.final_prob if score else None,
            legal_aid_flag=bool(score and score.legal_aid_flag),
            consent=_consent_label(t_list),
            last_contact=last,
            next_action="",
            diversion=diversion_dates(case.stay_start_date)
            if case.working_status == IN_DIVERSION
            else None,
        )
        row.next_action = (last.next_action if last and last.next_action else None) or (
            suggest_next_action(row, today)
        )
        rows.append(row)
    return rows


def sort_key(row: QueueRow):
    dth = row.days_to_hearing
    return (
        dth is None,
        dth if dth is not None else 0,
        POTENTIAL_ORDER.get(row.potential or "", 9),
        -(row.final_prob or 0.0),
        row.case.case_number,
    )


def call_list(
    session: Session,
    role: str,
    *,
    status: str | None = None,
    potential: str | None = None,
    pathway: str | None = None,
    show_closed: bool = False,
    search: str | None = None,
) -> list[QueueRow]:
    q = visible_case_query(role).options(selectinload(Case.parties))
    if not show_closed:
        q = q.where(Case.working_status.not_in(TERMINAL_STATUSES))
    if status:
        q = q.where(Case.working_status == status)
    if search:
        q = q.where(Case.case_number.contains(search.strip()))
    cases = list(session.scalars(q))
    rows = build_rows(session, cases)
    if potential:
        rows = [r for r in rows if r.potential == potential]
    if pathway:
        rows = [r for r in rows if r.pathway == pathway]
    rows.sort(key=sort_key)
    return rows


def days_until(when: datetime | None) -> int | None:
    return None if when is None else (when.date() - clock.today()).days
