"""The scenario suite from PROMPT.md section G — one test per row, named after the row."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select

from core.constants import CLERK, FACILITATOR, IN_DIVERSION, JUDGE, TERMINAL_STATUSES
from core.models import Case, FeedChange, ImportRejection
from core.queue import call_list
from core.status import diversion_dates
from tests.factories import import_rows, row

# --- Import / sync scenarios (Milestone 1) ---------------------------------------------------


def test_malformed_row_or_wrong_case_type_rejected_with_reason(session):
    rows = [
        row(1),
        row(2, case_number="97K01-2603-EV-12"),  # bad pattern
        row(3, case_type="SC"),  # wrong case type
        row(4, defendant_count=2),  # count mismatch
    ]
    run = import_rows(session, rows)
    assert (run.inserted, run.rejected) == (1, 3)
    reasons = {r.row_number: " ".join(r.reasons) for r in session.scalars(select(ImportRejection))}
    assert "does not match the expected pattern" in reasons[3]
    assert "case_type is 'SC', expected 'EV'" in reasons[4]
    assert "defendant_count is 2 but 1 defendant name(s)" in reasons[5]
    assert session.scalar(select(Case).where(Case.case_type == "SC")) is None


def test_same_file_imported_twice_no_changes(session):
    rows = [row(i) for i in range(1, 6)] + [row(9, case_type="XX")]
    first = import_rows(session, rows)
    before = {
        c.case_number: (c.row_hash, c.updated_at, c.working_status)
        for c in session.scalars(select(Case))
    }
    second = import_rows(session, rows)
    assert first.inserted == 5
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 5)
    after = {
        c.case_number: (c.row_hash, c.updated_at, c.working_status)
        for c in session.scalars(select(Case))
    }
    assert before == after
    assert session.scalars(select(FeedChange)).all() == []


def test_continuance_in_next_import_new_hearing_date_and_queue_resorted(session):
    import_rows(
        session,
        [
            row(1, hearing_datetime="2026-03-18T09:00"),  # soonest
            row(2, hearing_datetime="2026-03-20T09:00"),
        ],
    )
    order = [r.case.case_number for r in call_list(session, FACILITATOR)]
    assert order == ["97K01-2603-EV-000001", "97K01-2603-EV-000002"]

    import_rows(
        session,
        [
            row(
                1,
                hearing_datetime="2026-04-08T09:00",
                latest_event="continued",
                latest_event_date="2026-03-16",
            ),
            row(2, hearing_datetime="2026-03-20T09:00"),
        ],
        filename="day2.csv",
    )
    case = session.scalar(select(Case).where(Case.case_number == "97K01-2603-EV-000001"))
    assert case.hearing_datetime == datetime(2026, 4, 8, 9, 0)
    assert case.first_hearing_datetime == datetime(2026, 3, 18, 9, 0)  # as-of rule keeps first
    order = [r.case.case_number for r in call_list(session, FACILITATOR)]
    assert order == ["97K01-2603-EV-000002", "97K01-2603-EV-000001"]


def test_dismissal_before_hearing_case_closed_and_removed_from_call_list(session):
    import_rows(session, [row(1), row(2)])
    assert len(call_list(session, FACILITATOR)) == 2
    import_rows(
        session,
        [
            row(
                1,
                latest_event="dismissed",
                latest_event_date="2026-03-15",
                disposition_type="dismissed_on_payment",
                disposition_date="2026-03-15",
                sealed_flag="true",
            ),
            row(2),
        ],
    )
    case = session.scalar(select(Case).where(Case.case_number == "97K01-2603-EV-000001"))
    assert case.working_status in TERMINAL_STATUSES
    assert case.disposition_date == date(2026, 3, 15)
    listed = [r.case.case_number for r in call_list(session, FACILITATOR)]
    assert listed == ["97K01-2603-EV-000002"]


def test_diversion_stay_in_feed_status_in_diversion_with_30_60_dates(session):
    import_rows(session, [row(1)])
    import_rows(
        session,
        [
            row(
                1,
                latest_event="diversion_stay",
                latest_event_date="2026-03-16",
                confidential_flag="true",
            )
        ],
    )
    case = session.scalar(select(Case))
    assert case.working_status == IN_DIVERSION
    dates = diversion_dates(case.stay_start_date)
    assert dates["check_30"] == date(2026, 4, 15)
    assert dates["check_60"] == date(2026, 5, 15)
    # Facilitators see the restricted case in the queue with its 30/60-day dates.
    (queued,) = call_list(session, FACILITATOR)
    assert queued.diversion["check_30"] == date(2026, 4, 15)


def test_case_sealed_in_feed_hidden_from_clerk_and_judge(session):
    import_rows(session, [row(1), row(2)])
    assert len(call_list(session, CLERK)) == 2
    run = import_rows(session, [row(1, sealed_flag="true"), row(2)])
    assert run.newly_restricted == 1
    for role in (CLERK, JUDGE):
        assert [r.case.case_number for r in call_list(session, role)] == ["97K01-2603-EV-000002"]
    assert len(call_list(session, FACILITATOR)) == 2
