"""Builders for small synthetic export rows used by the tests."""

from __future__ import annotations

import csv
import io

from core.importer import EXPORT_COLUMNS, import_csv

PLAINTIFF = "a1b2c3d4e5f60718"


def row(seq: int = 1, **overrides) -> dict:
    base = {
        "case_number": f"97K01-2603-EV-{seq:06d}",
        "court_id": "97K01",
        "filing_date": "2026-03-02",
        "case_type": "EV",
        "amount_claimed": "1850.00",
        "hearing_datetime": "2026-03-30T09:00",
        "plaintiff_id": PLAINTIFF,
        "plaintiff_attorney": "true",
        "defendant_count": "1",
        "defendant_names": f"Ann Tester{seq}",
        "phone": "317-555-0101",
        "mailing_address": f"{seq} Test St Apt 3, Faketown, IN 46000",
        "latest_event": "served",
        "disposition_type": "",
        "disposition_date": "",
        "confidential_flag": "false",
        "sealed_flag": "false",
        "latest_event_date": "2026-03-06",
        "plaintiff_name": "Testing Rentals LLC",
        "export_date": "2026-03-15",
    }
    base.update({k: str(v) for k, v in overrides.items()})
    return base


def csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(EXPORT_COLUMNS))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


def import_rows(session, rows: list[dict], filename: str = "test.csv"):
    run = import_csv(session, csv_bytes(rows), filename=filename)
    session.commit()
    return run


def tenant_form(**overrides) -> dict:
    form = {
        "believed_owed": "1200",
        "pay_30d": "400",
        "pay_monthly": "250",
        "assistance_status": "pending",
        "assistance_amount": "",
        "wants": "stay",
        "willing_mediator": "yes",
        "legal_aid": "no",
        "consent_text": "on",
        "preferred_language": "English",
        "lang": "en",
    }
    form.update(overrides)
    return form


def landlord_form(**overrides) -> dict:
    form = {
        "amount_owed_now": "1500",
        "fees_included": "150",
        "accepts_plan": "yes",
        "max_plan_months": "4",
        "min_to_dismiss": "1300",
        "waive_late_fees": "on",
        "extend_moveout": "no",
        "goal": "rent",
        "willing_mediator": "yes",
        "lang": "en",
    }
    form.update(overrides)
    return form
