"""What happens to a case after the feed (or an intake) changes it.

Order matters: parties/tokens -> landlord standing policy -> working status ->
reconciliation -> scoring.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from core.constants import TERMINAL_EVENTS
from core.intake import apply_policy_to_case
from core.models import Case, ImportRun
from core.parties import ensure_parties
from core.reconcile import check_unconfirmed_diversions, reconcile_case
from core.status import refresh_status


def _chunks(items: list, size: int = 500):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def after_import(session: Session, run: ImportRun, changed: dict) -> None:
    flags = 0
    newly_restricted = 0
    ids = list(changed)
    for chunk in _chunks(ids):
        cases = session.scalars(
            select(Case).where(Case.id.in_(chunk)).options(selectinload(Case.parties))
        )
        for case in cases:
            old = changed[case.id]
            if case.latest_event not in TERMINAL_EVENTS:
                ensure_parties(session, case)
            apply_policy_to_case(session, case)
            refresh_status(session, case, source="feed", is_new=old.is_new, import_id=run.id)
            if not old.is_new:
                flags += len(
                    reconcile_case(
                        session,
                        run,
                        case,
                        old_status=old.status,
                        old_staff_status=old.staff_status,
                        old_event=old.event,
                        old_restricted=old.restricted,
                    )
                )
                if case.is_restricted and not old.restricted:
                    newly_restricted += 1
    flags += len(check_unconfirmed_diversions(session, run))
    run.reconciliation_flags = flags
    run.newly_restricted = newly_restricted
    session.flush()
    rescore(session, ids)


def after_intake(
    session: Session, cases: list[Case], *, source: str = "system", user_id: int | None = None
) -> None:
    """After answers or a policy change: refresh working status, then rescore."""
    for case in cases:
        refresh_status(session, case, source=source, user_id=user_id)
    session.flush()
    rescore(session, [c.id for c in cases])


def rescore(session: Session, case_ids: list[int]) -> None:
    """Recompute scores for open cases (Milestone 3)."""
    try:
        from core.scoring_service import rescore_cases
    except ImportError:  # pragma: no cover - before Milestone 3
        return
    rescore_cases(session, case_ids)
