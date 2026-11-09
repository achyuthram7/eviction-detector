"""Working-status state machine. Official feed events always override the tool's own status."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core import clock
from core.constants import (
    BOTH,
    CLOSED,
    IN_DIVERSION,
    INVITED,
    NEW,
    NO_SETTLEMENT,
    PARTIAL,
    SETTLED,
    TERMINAL_EVENTS,
)
from core.models import Case, LandlordResponse, StatusHistory, TenantResponse
from core.settings import get_settings

_EVENT_FALLBACK = {
    "agreed_judgment": (SETTLED, None),
    "dismissed": (CLOSED, "dismissed"),
    "default_judgment": (NO_SETTLEMENT, "default judgment"),
    "judgment_after_trial": (NO_SETTLEMENT, "judgment after trial"),
    "writ_issued": (NO_SETTLEMENT, "writ issued"),
}


def is_resolved(latest_event: str | None, disposition_type: str | None) -> bool | None:
    """Model label: True = resolved/settled, False = not, None = still open."""
    cfg = get_settings().app
    if latest_event not in TERMINAL_EVENTS:
        return None
    if disposition_type and disposition_type in cfg["dispositions"]:
        return bool(cfg["dispositions"][disposition_type]["resolved"])
    return latest_event in set(cfg["resolved_events"])


def feed_status(case: Case) -> tuple[str, str | None] | None:
    """Status dictated by the official feed, or None when the feed leaves it to the tool."""
    if case.latest_event in TERMINAL_EVENTS:
        dispositions = get_settings().app["dispositions"]
        if case.disposition_type and case.disposition_type in dispositions:
            d = dispositions[case.disposition_type]
            return d["status"], d.get("subtype")
        return _EVENT_FALLBACK[case.latest_event]
    if case.latest_event == "diversion_stay":
        return IN_DIVERSION, None
    return None


def response_flags(session: Session, case: Case) -> tuple[bool, bool]:
    has_tenant = (
        session.scalar(
            select(func.count(TenantResponse.id)).where(TenantResponse.case_id == case.id)
        )
        or 0
    ) > 0
    has_landlord = (
        session.scalar(
            select(func.count(LandlordResponse.id)).where(LandlordResponse.case_id == case.id)
        )
        or 0
    ) > 0
    return has_tenant, has_landlord


def activity_status(case: Case, has_tenant: bool, has_landlord: bool) -> str:
    if case.staff_status:
        return case.staff_status
    if has_tenant and has_landlord:
        return BOTH
    if has_tenant or has_landlord:
        return PARTIAL
    if case.invited_at:
        return INVITED
    return NEW


def compute_status(session: Session, case: Case) -> tuple[str, str | None]:
    official = feed_status(case)
    if official is not None:
        return official
    has_tenant, has_landlord = response_flags(session, case)
    return activity_status(case, has_tenant, has_landlord), None


def refresh_status(
    session: Session,
    case: Case,
    *,
    source: str,
    is_new: bool = False,
    import_id: int | None = None,
    user_id: int | None = None,
    note: str | None = None,
) -> bool:
    """Recompute the working status; write history when it changes. Returns True on change."""
    old_status = case.working_status
    old_subtype = case.settled_subtype
    new_status, subtype = compute_status(session, case)
    if new_status == IN_DIVERSION and case.stay_start_date is None:
        case.stay_start_date = case.latest_event_date or clock.today()
    if case.latest_event in TERMINAL_EVENTS:
        case.staff_status = None  # the official outcome supersedes staff statuses
    if not is_new and (old_status, old_subtype) == (new_status, subtype):
        return False
    case.working_status = new_status
    case.settled_subtype = subtype
    session.add(
        StatusHistory(
            case_id=case.id,
            from_status=None if is_new else old_status,
            to_status=new_status,
            subtype=subtype,
            source=source,
            import_id=import_id,
            user_id=user_id,
            at=clock.now(),
            note=note,
        )
    )
    return True


def diversion_dates(stay_start: date | None) -> dict[str, date] | None:
    if stay_start is None:
        return None
    cfg = get_settings().app["diversion"]
    first, second = cfg["status_check_days"]
    return {
        "stay_start": stay_start,
        "check_30": stay_start + timedelta(days=first),
        "check_60": stay_start + timedelta(days=second),
        "stay_end": stay_start + timedelta(days=cfg["stay_days"]),
    }
