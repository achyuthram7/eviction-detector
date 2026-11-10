"""Audit log: who did what, when. Every case view is recorded (incl. restricted cases)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from core import clock
from core.models import AuditLog, Case, User


def log(
    session: Session,
    user: User | None,
    action: str,
    *,
    case: Case | None = None,
    details: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        at=clock.now(),
        user_id=user.id if user else None,
        username=user.username if user else None,
        role=user.role if user else None,
        action=action,
        case_id=case.id if case else None,
        restricted=bool(case and case.is_restricted),
        details=details,
    )
    session.add(entry)
    return entry
