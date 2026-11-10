"""'Both agreed to diversion': the Odyssey checklist and a draft order, for staff to copy.

Text comes from config/diversion_checklist.yaml and is a PLACEHOLDER until the court confirms
it (PLAN.md Q B1). Nothing is sent anywhere; the tool never writes to Odyssey.
"""

from __future__ import annotations

from datetime import date

from core.models import Case
from core.settings import get_settings
from core.status import diversion_dates


def build_checklist(case: Case, start: date) -> dict:
    cfg = get_settings().diversion
    stay_days = get_settings().app["diversion"]["stay_days"]
    dates = diversion_dates(start)
    fmt = {k: v.strftime("%A, %B %d, %Y") for k, v in dates.items()}
    values = dict(fmt, case_number=case.case_number, court_id=case.court_id, stay_days=stay_days)
    return {
        "notice": cfg["notice"],
        "dates": dates,
        "items": [item.format(**values) for item in cfg["odyssey_checklist"]],
        "draft_order": cfg["draft_order"].format(**values),
    }
