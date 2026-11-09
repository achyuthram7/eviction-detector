"""Reconciliation: compare the tool's working status with the latest official event."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import clock
from core.constants import (
    BOTH,
    IN_DIVERSION,
    NO_SETTLEMENT,
    PARTIAL,
    REFERRED,
    TERMINAL_EVENTS,
    TERMINAL_STATUSES,
)
from core.models import Case, ImportRun, ReconciliationFlag
from core.settings import get_settings

# Statuses that mean staff are actively working the case; a feed outcome replacing one of
# these is worth telling staff about ("stop calling, the case was dismissed").
_ACTIVE_WORK = {REFERRED, BOTH, PARTIAL, NO_SETTLEMENT}


def _flag(session, run, case, kind, before, message) -> ReconciliationFlag:
    flag = ReconciliationFlag(
        import_id=run.id if run else None,
        case_id=case.id,
        kind=kind,
        working_status_before=before,
        feed_event=case.latest_event,
        message=message,
        created_at=clock.now(),
    )
    session.add(flag)
    return flag


def reconcile_case(
    session: Session,
    run: ImportRun | None,
    case: Case,
    *,
    old_status: str | None,
    old_staff_status: str | None,
    old_event: str | None,
    old_restricted: bool,
) -> list[ReconciliationFlag]:
    """Flags for an existing case after a feed update (new cases are not reconciled)."""
    flags: list[ReconciliationFlag] = []
    new_status = case.working_status
    feed_decides = case.latest_event in TERMINAL_EVENTS or case.latest_event == "diversion_stay"
    if (
        feed_decides
        and new_status != old_status
        and (old_staff_status is not None or old_status in _ACTIVE_WORK)
    ):
        flags.append(
            _flag(
                session,
                run,
                case,
                "feed_override",
                old_status,
                f"Working status '{old_status}' was replaced by the official event "
                f"'{case.latest_event}' ({new_status}).",
            )
        )
    if old_event in TERMINAL_EVENTS and case.latest_event not in TERMINAL_EVENTS:
        flags.append(
            _flag(
                session,
                run,
                case,
                "event_regressed",
                old_status,
                f"Official event went from '{old_event}' back to '{case.latest_event}' "
                "(reopened or corrected?). Check Odyssey.",
            )
        )
    if old_status == IN_DIVERSION and new_status not in TERMINAL_STATUSES | {IN_DIVERSION}:
        flags.append(
            _flag(
                session,
                run,
                case,
                "diversion_ended",
                old_status,
                f"The feed no longer shows a diversion stay (now '{case.latest_event}').",
            )
        )
    if (
        old_status in TERMINAL_STATUSES
        and new_status in TERMINAL_STATUSES
        and old_status != new_status
    ):
        flags.append(
            _flag(
                session,
                run,
                case,
                "outcome_changed",
                old_status,
                f"Official outcome changed from '{old_status}' to '{new_status}'.",
            )
        )
    if case.is_restricted and not old_restricted:
        flags.append(
            _flag(
                session,
                run,
                case,
                "restricted",
                old_status,
                "Case is now restricted (sealed/confidential): hidden from clerk and judge views.",
            )
        )
    return flags


def check_unconfirmed_diversions(
    session: Session, run: ImportRun | None
) -> list[ReconciliationFlag]:
    """Cases marked 'both agreed to diversion' in the tool that the feed has not confirmed."""
    days = get_settings().app["diversion"]["confirm_within_days"]
    cutoff = clock.now() - timedelta(days=days)
    flags: list[ReconciliationFlag] = []
    candidates = session.scalars(
        select(Case).where(
            Case.diversion_agreed_at.is_not(None), Case.diversion_agreed_at <= cutoff
        )
    )
    for case in candidates:
        if case.latest_event == "diversion_stay" or case.latest_event in TERMINAL_EVENTS:
            continue
        open_flag = session.scalar(
            select(ReconciliationFlag.id).where(
                ReconciliationFlag.case_id == case.id,
                ReconciliationFlag.kind == "diversion_unconfirmed",
                ReconciliationFlag.resolved_at.is_(None),
            )
        )
        if open_flag is None:
            flags.append(
                _flag(
                    session,
                    run,
                    case,
                    "diversion_unconfirmed",
                    case.working_status,
                    "Diversion agreement recorded in the tool on "
                    f"{case.diversion_agreed_at:%Y-%m-%d} but the feed has not shown a "
                    f"diversion stay after {days} days.",
                )
            )
    return flags
