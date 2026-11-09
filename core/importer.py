"""Import a (simulated) Odyssey daily export CSV.

* Validates every row and rejects malformed ones with reasons (import report).
* Upserts by case_number. A row whose normalized content is unchanged is not written at all,
  so importing the same file twice changes nothing.
* Changed cases go through core.pipeline (status, reconciliation, policies, scoring).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import clock
from core.constants import EVENTS, TERMINAL_EVENTS
from core.models import Case, FeedChange, ImportRejection, ImportRun, Plaintiff
from core.normalize import (
    parse_amount,
    parse_bool,
    parse_date,
    parse_datetime,
    split_list,
)
from core.settings import get_settings

REQUIRED_COLUMNS = (
    "case_number",
    "court_id",
    "filing_date",
    "case_type",
    "amount_claimed",
    "hearing_datetime",
    "plaintiff_id",
    "plaintiff_attorney",
    "defendant_count",
    "defendant_names",
    "mailing_address",
    "latest_event",
    "disposition_type",
    "disposition_date",
    "confidential_flag",
    "sealed_flag",
)
# Optional additions to the export (PLAN.md Q A2). first_hearing_datetime keeps the as-of rule
# honest for cases first seen after a continuance (e.g. closed history).
OPTIONAL_COLUMNS = (
    "phone",
    "latest_event_date",
    "plaintiff_name",
    "export_date",
    "first_hearing_datetime",
)
EXPORT_COLUMNS = (
    "case_number",
    "court_id",
    "filing_date",
    "case_type",
    "amount_claimed",
    "hearing_datetime",
    "first_hearing_datetime",
    "plaintiff_id",
    "plaintiff_attorney",
    "defendant_count",
    "defendant_names",
    "phone",
    "mailing_address",
    "latest_event",
    "latest_event_date",
    "disposition_type",
    "disposition_date",
    "confidential_flag",
    "sealed_flag",
    "plaintiff_name",
    "export_date",
)

# Case attributes that come from the feed (compared and copied on every import).
CASE_FIELDS = (
    "court_id",
    "case_type",
    "filing_date",
    "amount_claimed",
    "amount_is_placeholder",
    "hearing_datetime",
    "plaintiff_id",
    "plaintiff_attorney",
    "defendant_count",
    "defendant_names",
    "mailing_address",
    "phone",
    "latest_event",
    "latest_event_date",
    "disposition_type",
    "disposition_date",
    "confidential_flag",
    "sealed_flag",
)


@dataclass
class OldState:
    is_new: bool
    status: str | None
    staff_status: str | None
    event: str | None
    restricted: bool


def _date(value: str, field: str, reasons: list[str]) -> date | None:
    try:
        return parse_date(value)
    except ValueError:
        reasons.append(f"{field} '{value}' is not a valid date (YYYY-MM-DD)")
        return None


def validate_row(raw: dict[str, str | None]) -> tuple[dict | None, list[str]]:
    """Return (clean record, reasons). The record is None when any reason exists."""
    cfg = get_settings().app
    v = {k: (raw.get(k) or "").strip() for k in REQUIRED_COLUMNS + OPTIONAL_COLUMNS}
    reasons: list[str] = []

    for field in (
        "case_number",
        "court_id",
        "filing_date",
        "case_type",
        "plaintiff_id",
        "plaintiff_attorney",
        "defendant_count",
        "defendant_names",
        "mailing_address",
        "latest_event",
        "confidential_flag",
        "sealed_flag",
    ):
        if not v[field]:
            reasons.append(f"{field} is required")

    if v["case_number"] and not re.fullmatch(cfg["case_number_regex"], v["case_number"]):
        reasons.append(f"case_number '{v['case_number']}' does not match the expected pattern")
    if v["case_type"] and v["case_type"] != cfg["case_type"]:
        reasons.append(f"case_type is '{v['case_type']}', expected '{cfg['case_type']}'")

    filing_date = _date(v["filing_date"], "filing_date", reasons) if v["filing_date"] else None
    disposition_date = _date(v["disposition_date"], "disposition_date", reasons)
    latest_event_date = _date(v["latest_event_date"], "latest_event_date", reasons)
    export_date = _date(v["export_date"], "export_date", reasons)

    hearing = None
    if v["hearing_datetime"]:
        try:
            hearing = parse_datetime(v["hearing_datetime"])
        except ValueError:
            reasons.append(f"hearing_datetime '{v['hearing_datetime']}' is not a valid date/time")
    first_hearing = None
    if v["first_hearing_datetime"]:
        try:
            first_hearing = parse_datetime(v["first_hearing_datetime"])
        except ValueError:
            reasons.append(
                f"first_hearing_datetime '{v['first_hearing_datetime']}' is not a valid date/time"
            )

    amount = None
    try:
        amount = parse_amount(v["amount_claimed"])
        if amount is not None and amount < 0:
            reasons.append(f"amount_claimed '{v['amount_claimed']}' is negative")
    except ValueError:
        reasons.append(f"amount_claimed '{v['amount_claimed']}' is not a number")

    bools = {}
    for field in ("plaintiff_attorney", "confidential_flag", "sealed_flag"):
        if v[field]:
            parsed = parse_bool(v[field])
            if parsed is None:
                reasons.append(f"{field} '{v[field]}' is not true/false")
            bools[field] = bool(parsed)

    names = [n for n in split_list(v["defendant_names"]) if n]
    count = None
    if v["defendant_count"]:
        try:
            count = int(v["defendant_count"])
            if count < 1:
                reasons.append("defendant_count must be at least 1")
            elif names and count != len(names):
                reasons.append(
                    f"defendant_count is {count} but {len(names)} defendant name(s) were given"
                )
        except ValueError:
            reasons.append(f"defendant_count '{v['defendant_count']}' is not a whole number")

    if v["latest_event"] and v["latest_event"] not in EVENTS:
        reasons.append(f"latest_event '{v['latest_event']}' is not a recognised event")
    if v["latest_event"] in TERMINAL_EVENTS and not disposition_date:
        disposition_date = latest_event_date
        if disposition_date is None:
            reasons.append(f"latest_event '{v['latest_event']}' needs a disposition_date")

    if filing_date:
        if hearing and hearing.date() < filing_date:
            reasons.append("hearing_datetime is before filing_date")
        if disposition_date and disposition_date < filing_date:
            reasons.append("disposition_date is before filing_date")

    if reasons:
        return None, reasons

    phones = split_list(v["phone"])
    record = {
        "case_number": v["case_number"],
        "court_id": v["court_id"],
        "case_type": v["case_type"],
        "filing_date": filing_date,
        "amount_claimed": amount,
        "amount_is_placeholder": amount is not None and amount >= float(cfg["claim_cap"]),
        "hearing_datetime": hearing,
        "plaintiff_id": v["plaintiff_id"],
        "plaintiff_name": v["plaintiff_name"] or None,
        "plaintiff_attorney": bools["plaintiff_attorney"],
        "defendant_count": count,
        "defendant_names": "; ".join(names),
        "mailing_address": v["mailing_address"],
        "phone": "; ".join(phones) if any(phones) else None,
        "latest_event": v["latest_event"],
        "latest_event_date": latest_event_date,
        "disposition_type": v["disposition_type"] or None,
        "disposition_date": disposition_date,
        "confidential_flag": bools["confidential_flag"],
        "sealed_flag": bools["sealed_flag"],
        "export_date": export_date,
        "first_hearing_datetime": first_hearing,
    }
    return record, []


def row_hash(record: dict) -> str:
    """Hash of everything except export_date (which changes every day)."""
    payload = {k: record[k] for k in sorted(record) if k != "export_date"}
    return hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()


def _is_restricted(record_or_case) -> bool:
    cfg = get_settings().app
    get = (
        record_or_case.get
        if isinstance(record_or_case, dict)
        else lambda k: getattr(record_or_case, k)
    )
    return bool(
        get("confidential_flag")
        or get("sealed_flag")
        or get("latest_event") in set(cfg.get("restrict_on_events") or [])
    )


def _fmt(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _read_text(source: Path | str | bytes) -> tuple[str, bytes]:
    if isinstance(source, bytes):
        raw = source
    else:
        raw = Path(source).read_bytes()
    return raw.decode("utf-8-sig"), raw


def import_csv(
    session: Session,
    source: Path | str | bytes,
    *,
    filename: str | None = None,
    user_id: int | None = None,
) -> ImportRun:
    from core import pipeline  # local import: pipeline depends on modules that import us

    text, raw = _read_text(source)
    if filename is None:
        filename = Path(source).name if not isinstance(source, bytes) else "upload.csv"
    now = clock.now()
    run = ImportRun(
        filename=filename,
        file_sha256=hashlib.sha256(raw).hexdigest(),
        imported_at=now,
        user_id=user_id,
    )
    session.add(run)
    session.flush()

    reader = csv.DictReader(io.StringIO(text))
    columns = [c.strip() for c in (reader.fieldnames or [])]
    reader.fieldnames = columns
    rows = list(reader)
    run.rows_total = len(rows)
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        run.file_error = "Missing required column(s): " + ", ".join(missing)
        run.rejected = len(rows)
        return run

    numbers = [(r.get("case_number") or "").strip() for r in rows]
    existing: dict[str, Case] = {}
    for i in range(0, len(numbers), 500):
        chunk = [n for n in numbers[i : i + 500] if n]
        for case in session.scalars(select(Case).where(Case.case_number.in_(chunk))):
            existing[case.case_number] = case
    plaintiff_ids = {(r.get("plaintiff_id") or "").strip() for r in rows} - {""}
    plaintiffs: dict[str, Plaintiff] = {}
    ids = list(plaintiff_ids)
    for i in range(0, len(ids), 500):
        for p in session.scalars(
            select(Plaintiff).where(Plaintiff.plaintiff_id.in_(ids[i : i + 500]))
        ):
            plaintiffs[p.plaintiff_id] = p

    seen: dict[str, int] = {}
    new_cases: list[Case] = []
    changed: dict[int, OldState] = {}
    export_dates: set[date] = set()

    for row_number, raw_row in enumerate(rows, start=2):  # row 1 is the header
        record, reasons = validate_row(raw_row)
        number = (raw_row.get("case_number") or "").strip()
        if record is not None and number in seen:
            reasons = [f"duplicate case_number in this file (first seen on row {seen[number]})"]
            record = None
        if record is None:
            session.add(
                ImportRejection(
                    import_id=run.id,
                    row_number=row_number,
                    case_number_raw=number or None,
                    reasons=reasons,
                )
            )
            run.rejected += 1
            continue
        seen[number] = row_number
        if record["export_date"]:
            export_dates.add(record["export_date"])

        pid = record["plaintiff_id"]
        plaintiff = plaintiffs.get(pid)
        if plaintiff is None:
            plaintiff = Plaintiff(
                plaintiff_id=pid, label=record["plaintiff_name"] or f"Landlord {pid[:6].upper()}"
            )
            session.add(plaintiff)
        elif record["plaintiff_name"] and plaintiff.label != record["plaintiff_name"]:
            plaintiff.label = record["plaintiff_name"]
        plaintiffs[pid] = plaintiff

        digest = row_hash(record)
        case = existing.get(number)
        if case is None:
            case = Case(
                case_number=number,
                row_hash=digest,
                first_hearing_datetime=record["first_hearing_datetime"]
                or record["hearing_datetime"],
                is_restricted=_is_restricted(record),
                working_status="New",
                first_import_id=run.id,
                last_import_id=run.id,
                created_at=now,
                updated_at=now,
                **{f: record[f] for f in CASE_FIELDS},
            )
            session.add(case)
            new_cases.append(case)
            existing[number] = case
            run.inserted += 1
            continue
        if case.row_hash == digest:
            run.unchanged += 1
            continue

        old = OldState(
            is_new=False,
            status=case.working_status,
            staff_status=case.staff_status,
            event=case.latest_event,
            restricted=case.is_restricted,
        )
        for field in CASE_FIELDS:
            if getattr(case, field) != record[field]:
                session.add(
                    FeedChange(
                        case_id=case.id,
                        import_id=run.id,
                        field=field,
                        old_value=_fmt(getattr(case, field)),
                        new_value=_fmt(record[field]),
                    )
                )
                setattr(case, field, record[field])
        if record["first_hearing_datetime"] is not None:
            case.first_hearing_datetime = record["first_hearing_datetime"]  # feed is authoritative
        elif case.first_hearing_datetime is None and case.hearing_datetime is not None:
            case.first_hearing_datetime = case.hearing_datetime
        case.is_restricted = _is_restricted(case)
        case.row_hash = digest
        case.last_import_id = run.id
        case.updated_at = now
        changed[case.id] = old
        run.updated += 1

    if len(export_dates) == 1:
        run.export_date = export_dates.pop()
    session.flush()
    for case in new_cases:
        changed[case.id] = OldState(True, None, None, None, case.is_restricted)
    pipeline.after_import(session, run, changed)
    return run


def write_export_csv(path: Path, rows: list[dict]) -> None:
    """Write rows in the export column order (used by the synthetic generator)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(EXPORT_COLUMNS), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
