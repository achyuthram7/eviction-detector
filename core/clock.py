"""The app's notion of "today". APP_TODAY=YYYY-MM-DD freezes the date (demos and tests).

now() keeps the real time of day and real elapsed time, shifted to the frozen date, so intake
timings stay accurate even when the date is frozen.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta


def _override() -> date | None:
    value = os.environ.get("APP_TODAY", "").strip()
    return date.fromisoformat(value) if value else None


def today() -> date:
    return _override() or date.today()


def now() -> datetime:
    real = datetime.now().replace(microsecond=0)
    frozen = _override()
    if frozen is None:
        return real
    return real + timedelta(days=(frozen - real.date()).days)
