"""Importer details beyond the section-G scenarios: reconciliation, edge rows, file errors."""

from __future__ import annotations

from sqlalchemy import select

from core.constants import CLOSED, NEW, REFERRED, SETTLED, TENANT
from core.models import Case, Party, ReconciliationFlag, StatusHistory
from core.status import refresh_status
from tests.factories import csv_bytes, import_rows, row


def test_new_case_gets_parties_with_random_tokens(session):
    import_rows(
        session,
        [row(1, defendant_count=2, defendant_names="Ann One; Bo Two", phone="317-555-0101; ")],
    )
    case = session.scalar(select(Case))
    tenants = [p for p in case.parties if p.role == TENANT]
    assert [p.full_name for p in tenants] == ["Ann One", "Bo Two"]
    assert tenants[0].phone == "317-555-0101" and tenants[1].phone is None
    tokens = [p.token for p in case.parties]
    assert len(set(tokens)) == 3
    assert all(case.case_number not in t and len(t) >= 20 for t in tokens)
    assert case.working_status == NEW


def test_export_date_alone_does_not_count_as_a_change(session):
    import_rows(session, [row(1)])
    run = import_rows(session, [row(1, export_date="2026-03-16")])
    assert (run.updated, run.unchanged) == (0, 1)


def test_referred_case_dismissed_in_feed_is_flagged(session):
    import_rows(session, [row(1)])
    case = session.scalar(select(Case))
    case.staff_status = REFERRED
    refresh_status(session, case, source="staff")
    session.commit()
    run = import_rows(
        session,
        [
            row(
                1,
                latest_event="dismissed",
                disposition_type="dismissed_on_payment",
                disposition_date="2026-03-15",
                latest_event_date="2026-03-15",
            )
        ],
    )
    assert run.reconciliation_flags >= 1
    flag = session.scalar(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "feed_override")
    )
    assert flag.working_status_before == REFERRED
    assert "dismissed" in flag.message
    assert case.working_status == SETTLED and case.staff_status is None


def test_unknown_dismissal_reason_is_closed_not_settled(session):
    import_rows(
        session,
        [
            row(
                1,
                latest_event="dismissed",
                disposition_date="2026-03-10",
                latest_event_date="2026-03-10",
            )
        ],
    )
    assert session.scalar(select(Case)).working_status == CLOSED


def test_event_regression_is_flagged(session):
    import_rows(
        session,
        [
            row(
                1,
                latest_event="dismissed",
                disposition_type="dismissed_other",
                disposition_date="2026-03-10",
            )
        ],
    )
    import_rows(session, [row(1, latest_event="hearing_set")])
    kinds = {f.kind for f in session.scalars(select(ReconciliationFlag))}
    assert "event_regressed" in kinds


def test_missing_column_is_a_file_error(session):
    data = csv_bytes([row(1)]).decode().replace("case_type", "kind").encode()
    from core.importer import import_csv

    run = import_csv(session, data, filename="bad.csv")
    assert run.file_error and "case_type" in run.file_error
    assert run.rejected == 1 and run.inserted == 0


def test_duplicate_case_number_in_one_file_rejected(session):
    run = import_rows(session, [row(1), row(1, amount_claimed="10.00")])
    assert (run.inserted, run.rejected) == (1, 1)
    assert "duplicate case_number" in run.rejections[0].reasons[0]


def test_ten_thousand_cap_marked_as_placeholder(session):
    import_rows(session, [row(1, amount_claimed="10000.00"), row(2, amount_claimed="2500")])
    cases = {c.case_number[-1]: c for c in session.scalars(select(Case))}
    assert cases["1"].amount_is_placeholder is True
    assert cases["2"].amount_is_placeholder is False


def test_status_history_written_on_change(session):
    import_rows(session, [row(1)])
    import_rows(
        session,
        [
            row(
                1,
                latest_event="default_judgment",
                disposition_type="default_judgment",
                disposition_date="2026-03-15",
            )
        ],
    )
    hist = session.scalars(select(StatusHistory).order_by(StatusHistory.id)).all()
    assert [(h.from_status, h.to_status) for h in hist] == [(None, NEW), (NEW, "No settlement")]
    assert all(h.source == "feed" for h in hist)


def test_terminal_case_at_first_import_gets_no_tokens(session):
    import_rows(
        session,
        [
            row(
                1,
                latest_event="agreed_judgment",
                disposition_type="agreed_move_out",
                disposition_date="2026-03-10",
            )
        ],
    )
    assert session.scalars(select(Party)).all() == []
