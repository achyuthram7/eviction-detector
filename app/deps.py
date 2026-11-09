"""Request dependencies: database session (route handlers commit explicitly)."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from core.db import new_session


def get_db() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
    finally:
        session.close()  # anything not committed by the handler is rolled back
