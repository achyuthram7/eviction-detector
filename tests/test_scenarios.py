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


# --- Party links and intake scenarios (Milestone 2) ------------------------------------------

from core.constants import LANDLORD, TENANT  # noqa: E402
from core.models import (  # noqa: E402
    FallbackSubmission,
    LandlordPolicy,
    LandlordResponse,
    Party,
    Plaintiff,
    TenantResponse,
)
from tests.factories import PLAINTIFF, landlord_form, tenant_form  # noqa: E402


def _party(session, role, index=None, case_number="97K01-2603-EV-000001") -> Party:
    session.expire_all()
    case = session.scalar(select(Case).where(Case.case_number == case_number))
    return next(
        p for p in case.parties if p.role == role and (index is None or p.party_index == index)
    )


def _open_and_submit(client, token, form):
    page = client.get(f"/r/{token}")
    assert page.status_code == 200
    timing_id = page.text.split('name="timing_id" value="')[1].split('"')[0]
    return client.post(f"/r/{token}", data={**form, "timing_id": timing_id}, follow_redirects=False)


def test_tenant_opens_token_link_and_submits_answer_attached_to_correct_case_and_party(
    session, client
):
    import_rows(session, [row(1), row(2)])
    tenant = _party(session, TENANT)
    resp = _open_and_submit(client, tenant.token, tenant_form(pay_30d="425"))
    assert resp.status_code == 303 and resp.headers["location"].startswith(f"/r/{tenant.token}")
    session.expire_all()
    (answer,) = session.scalars(select(TenantResponse)).all()
    assert answer.party_id == tenant.id
    assert answer.case_id == tenant.case_id
    assert answer.pay_30d == 425.0 and answer.source == "token" and answer.verified
    assert answer.started_at is not None and answer.submitted_at >= answer.started_at


def test_valid_case_number_entered_by_non_party_no_details_shown_sent_to_review_queue(
    session, client
):
    import_rows(session, [row(1, amount_claimed="2345.67", hearing_datetime="2026-03-30T09:00")])
    page = client.post(
        "/find",
        data={
            **tenant_form(),
            "role": "tenant",
            "case_number": "97K01-2603-EV-000001",
            "last_name": "Stranger",
        },
    )
    assert page.status_code == 200
    for secret in ("Ann Tester1", "2345", "2026-03-30", "Testing Rentals", "Test St"):
        assert secret not in page.text
    sub = session.scalar(select(FallbackSubmission))
    assert sub.status == "review" and sub.matched_party_id is None
    assert session.scalars(select(TenantResponse)).all() == []
    # A non-existent case number produces exactly the same page.
    other = client.post(
        "/find",
        data={
            **tenant_form(),
            "role": "tenant",
            "case_number": "99K01-2603-EV-999999",
            "last_name": "Stranger",
        },
    )
    assert other.text.split("<main")[1] == page.text.split("<main")[1]


def test_same_token_used_twice_update_not_duplicate(session, client):
    import_rows(session, [row(1)])
    tenant = _party(session, TENANT)
    _open_and_submit(client, tenant.token, tenant_form(pay_30d="100"))
    _open_and_submit(
        client, tenant.token, tenant_form(pay_30d="300", wants="move", move_time_days="30")
    )
    session.expire_all()
    answers = session.scalars(select(TenantResponse)).all()
    assert len(answers) == 1
    assert answers[0].pay_30d == 300.0 and answers[0].wants == "move"
    assert answers[0].revision == 2


def test_two_tenants_on_one_case_answer_differently_both_records_kept(session, client):
    import_rows(session, [row(1, defendant_count=2, defendant_names="Ann One; Bo Two")])
    first, second = _party(session, TENANT, 1), _party(session, TENANT, 2)
    _open_and_submit(client, first.token, tenant_form(wants="stay", pay_30d="500"))
    _open_and_submit(client, second.token, tenant_form(wants="move", move_time_days="45"))
    session.expire_all()
    answers = {a.party_id: a for a in session.scalars(select(TenantResponse))}
    assert set(answers) == {first.id, second.id}
    assert {a.case_id for a in answers.values()} == {first.case_id}
    assert answers[first.id].wants == "stay" and answers[second.id].wants == "move"


def test_landlord_standing_policy_and_new_filing_policy_applied_automatically(session, client):
    import_rows(session, [row(1)])
    plaintiff = session.get(Plaintiff, PLAINTIFF)
    from core.parties import ensure_portfolio_token

    token = ensure_portfolio_token(session, plaintiff)
    session.commit()
    page = client.get(f"/policy/{token}")
    timing_id = page.text.split('name="timing_id" value="')[1].split('"')[0]
    resp = client.post(
        f"/policy/{token}",
        data={
            "min_to_dismiss_pct": "90",
            "accepts_plan": "yes",
            "max_plan_months": "5",
            "goal": "rent",
            "willing_mediator": "yes",
            "timing_id": timing_id,
        },
    )
    assert resp.status_code == 200
    # A new filing from the same plaintiff arrives in the next import.
    import_rows(session, [row(1), row(2)])
    session.expire_all()
    assert session.scalar(select(LandlordPolicy)).max_plan_months == 5
    new_case = session.scalar(select(Case).where(Case.case_number == "97K01-2603-EV-000002"))
    applied = session.scalar(
        select(LandlordResponse).where(LandlordResponse.case_id == new_case.id)
    )
    assert applied is not None and applied.source == "policy"
    assert applied.min_to_dismiss_pct == 90.0 and applied.max_plan_months == 5
    assert new_case.working_status == "Partial response"


def test_tenant_views_own_intake_never_sees_landlord_answers_and_vice_versa(session, client):
    import_rows(session, [row(1)])
    tenant, landlord = _party(session, TENANT), _party(session, LANDLORD)
    _open_and_submit(client, tenant.token, tenant_form(pay_30d="777", pay_monthly="333"))
    _open_and_submit(
        client, landlord.token, landlord_form(min_to_dismiss="4321", amount_owed_now="5678")
    )
    tenant_pages = [
        client.get(f"/r/{tenant.token}").text,
        client.get(f"/r/{tenant.token}/done").text,
    ]
    landlord_pages = [
        client.get(f"/r/{landlord.token}").text,
        client.get(f"/r/{landlord.token}/done").text,
    ]
    for text in tenant_pages:
        assert "4,321" not in text and "4321" not in text and "5,678" not in text
    assert "777" in tenant_pages[1]
    for text in landlord_pages:
        assert "777" not in text and "333" not in text
    assert "4,321" in landlord_pages[1]


# --- Predictor scenarios (Milestone 3) -------------------------------------------------------

from core.constants import NEEDS_OUTREACH, NO_RESPONSE  # noqa: E402
from core.models import CaseScore  # noqa: E402
from core.scoring_service import rescore_cases  # noqa: E402


def _current_score(session, case_number="97K01-2603-EV-000001") -> CaseScore:
    session.expire_all()
    case = session.scalar(select(Case).where(Case.case_number == case_number))
    return session.scalar(
        select(CaseScore).where(CaseScore.case_id == case.id, CaseScore.is_current.is_(True))
    )


def test_neither_party_responds_no_response_needs_outreach_not_low(session, simple_model):
    simple_model(intercept=-3.0)  # base probability far below the Low threshold
    import_rows(session, [row(1)])
    score = _current_score(session)
    assert score.base_tier == "Low"
    assert score.potential == NO_RESPONSE and score.potential != "Low"
    assert score.pathway == NEEDS_OUTREACH
    assert 2 <= len(score.reasons) <= 4


def test_tenant_reports_repair_problems_legal_aid_flag_shown_with_settlement_result(
    session, client, simple_model
):
    simple_model()
    import_rows(session, [row(1)])
    tenant = _party(session, TENANT)
    _open_and_submit(
        client, tenant.token, tenant_form(repair_issues="on", repair_details="No heat")
    )
    score = _current_score(session)
    assert score.legal_aid_flag is True
    assert score.potential in ("High", "Medium", "Low")  # settlement result still shown
    assert 0 < score.final_prob < 1 and 2 <= len(score.reasons) <= 4


def test_rescoring_reuses_base_and_updates_layer2(session, simple_model):
    simple_model()
    import_rows(session, [row(1)])
    first = _current_score(session)
    rescore_cases(session, [first.case_id])
    session.commit()
    rows = session.scalars(select(CaseScore).where(CaseScore.case_id == first.case_id)).all()
    assert len(rows) == 2 and sum(r.is_current for r in rows) == 1
    assert rows[-1].base_prob == first.base_prob
