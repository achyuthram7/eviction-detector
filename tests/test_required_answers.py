"""Required questions and "Not sure" answers on the party forms, and the Tenant/Landlord
dropdown on /find."""

from __future__ import annotations

import re

from sqlalchemy import func, select

from core.constants import TENANT
from core.intake import (
    LANDLORD_NOT_SURE,
    LANDLORD_REQUIRED,
    POLICY_REQUIRED,
    TENANT_NOT_SURE,
    TENANT_REQUIRED,
    parse_landlord_form,
    parse_policy_form,
    parse_tenant_form,
)
from core.models import Case, FallbackSubmission, LandlordResponse, TenantResponse
from tests.factories import import_rows, landlord_form, row, tenant_form

MISSING = "Please answer this question."
FLAGGED = '<p class="error" id="err-'  # one per unanswered question


def _token(session, role: str = TENANT) -> str:
    session.expire_all()
    case = session.scalar(select(Case).where(Case.case_number == "97K01-2603-EV-000001"))
    return next(p for p in case.parties if p.role == role).token


def _tenant_token(session) -> str:
    return _token(session, TENANT)


# A tenant who isn't sure about anything that offers "Not sure".
TENANT_ALL_UNSURE = tenant_form(
    believed_owed="",
    believed_owed_unsure="on",
    pay_30d="lots",  # typed by mistake, then ticked "Not sure": the typing is ignored
    pay_30d_unsure="on",
    pay_monthly="",
    pay_monthly_unsure="on",
    assistance_status="unsure",
    wants="unsure",
    legal_aid="unsure",
)


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


# --- which questions are required ------------------------------------------------------------


def test_blank_forms_flag_exactly_the_required_questions():
    assert set(parse_tenant_form({}).errors) == set(TENANT_REQUIRED)
    assert set(parse_landlord_form({}).errors) == set(LANDLORD_REQUIRED)
    assert set(parse_policy_form({}).errors) == set(POLICY_REQUIRED)


def test_optional_questions_can_be_left_blank():
    form = tenant_form(assistance_amount="", contact_phone="", repair_details="")
    form.pop("consent_text")
    assert parse_tenant_form(form).ok
    assert parse_landlord_form(landlord_form(fees_included="", waive_late_fees="")).ok


def test_zero_and_no_are_answers_not_blanks():
    res = parse_tenant_form(tenant_form(pay_30d="0", pay_monthly="0", legal_aid="no"))
    assert res.ok and res.values["pay_30d"] == 0.0 and res.values["legal_aid"] is False


def test_follow_up_questions_required_only_when_they_apply():
    assert parse_tenant_form(tenant_form(wants="move")).errors == {"move_time_days": "err_required"}
    assert parse_tenant_form(tenant_form(wants="move", move_time_days="30")).ok
    assert parse_tenant_form(tenant_form(wants="stay", move_time_days="")).ok

    no_months = landlord_form(accepts_plan="yes", max_plan_months="")
    assert parse_landlord_form(no_months).errors == {"max_plan_months": "err_required"}
    assert parse_landlord_form(landlord_form(accepts_plan="no", max_plan_months="")).ok
    no_days = landlord_form(extend_moveout="yes")
    assert parse_landlord_form(no_days).errors == {"extend_moveout_days": "err_required"}


# --- "Not sure" ----------------------------------------------------------------------------


def test_not_sure_answers_every_question_that_offers_it():
    res = parse_tenant_form(TENANT_ALL_UNSURE)
    assert res.ok, res.errors
    assert res.unsure == set(TENANT_NOT_SURE) - {"move_time_days"}  # not moving, so not asked
    assert all(res.values[name] is None for name in res.unsure)  # saved as unknown

    landlord = landlord_form(
        amount_owed_now="",
        amount_owed_now_unsure="on",
        min_to_dismiss="",
        min_to_dismiss_unsure="on",
        accepts_plan="unsure",
        extend_moveout="unsure",
        goal="unsure",
    )
    res = parse_landlord_form(landlord)
    assert res.ok, res.errors
    assert res.unsure == set(LANDLORD_NOT_SURE) - {"max_plan_months", "extend_moveout_days"}
    assert res.values["max_plan_months"] is None  # only asked after "Yes"


def test_not_sure_works_for_follow_up_questions():
    res = parse_tenant_form(tenant_form(wants="move", move_time_days="unsure"))
    assert res.ok and res.unsure == {"move_time_days"}
    res = parse_landlord_form(
        landlord_form(accepts_plan="yes", max_plan_months="", max_plan_months_unsure="on")
    )
    assert res.ok and res.values["max_plan_months"] is None


def test_mediator_and_language_keep_their_own_answers():
    res = parse_tenant_form(tenant_form(willing_mediator="unsure", preferred_language="Other"))
    assert res.ok and res.values["willing_mediator"] == "unsure" and not res.unsure


def test_not_sure_is_saved_shown_back_and_reopened_selected(session, client):
    import_rows(session, [row(1)])
    token = _tenant_token(session)
    resp = client.post(f"/r/{token}", data=TENANT_ALL_UNSURE)
    assert resp.status_code == 200  # followed to the "your answers" page
    session.expire_all()
    saved = session.scalar(select(TenantResponse))
    assert saved.pay_30d is None and saved.wants is None and saved.legal_aid is None
    assert resp.text.count("Not sure") >= len(TENANT_NOT_SURE) - 1
    html = client.get(f"/r/{token}").text  # editing later: "Not sure" is still picked
    assert re.search(r'name="pay_30d_unsure" value="on"[^>]*checked', html)
    assert re.search(r'name="wants" value="unsure" checked', html)
    assert re.search(r'name="legal_aid" value="unsure" checked', html)


def test_not_sure_shows_on_the_judges_case_page(session, client):
    from tests.factories import login

    import_rows(session, [row(1)])
    client.post(
        f"/r/{_token(session, 'landlord')}",
        data=landlord_form(goal="unsure", min_to_dismiss="", min_to_dismiss_unsure="on"),
    )
    assert _count(session, LandlordResponse) == 1
    login(client, "judge")
    case_id = session.scalar(select(Case.id))
    html = client.get(f"/staff/cases/{case_id}").text
    assert "Not sure" in html


def test_not_sure_is_translated(session, client):
    import_rows(session, [row(1)])
    html = client.get(f"/r/{_tenant_token(session)}?lang=es").text
    assert "No estoy seguro/a" in html and "Si no sabe una respuesta" in html


# --- the forms ---------------------------------------------------------------------------------


def test_form_marks_required_fields_and_lets_the_browser_check_them(session, client):
    import_rows(session, [row(1)])
    html = client.get(f"/r/{_tenant_token(session)}").text
    assert "novalidate" not in html
    # Amounts with a "Not sure" box are required through app.js, so ticking it never blocks.
    assert re.search(r'name="pay_30d"[^>]*data-required', html)
    assert 'name="pay_30d_unsure"' in html
    assert re.search(r'name="wants" value="stay"[^>]*\srequired', html)
    assert not re.search(r'name="assistance_amount"[^>]*required', html)
    assert re.search(r'name="move_time_days"[^>]*data-required', html)  # only when moving
    assert "If you don&#39;t know an answer, choose “Not sure”." in html


def test_blank_submission_flags_each_question_and_saves_nothing(session, client):
    import_rows(session, [row(1)])
    resp = client.post(f"/r/{_tenant_token(session)}", data={"lang": "en"})
    assert resp.status_code == 422
    assert resp.text.count(FLAGGED) == len(TENANT_REQUIRED)
    assert f'<p class="error" id="err-pay_30d">{MISSING}</p>' in resp.text
    assert _count(session, TenantResponse) == 0


def test_required_message_is_translated(session, client):
    import_rows(session, [row(1)])
    resp = client.post(f"/r/{_tenant_token(session)}", data={"lang": "es"})
    assert resp.status_code == 422 and "Por favor, conteste esta pregunta." in resp.text


# --- /find: role dropdown and required case number / last name -----------------------------


def test_find_page_has_role_dropdown_with_other_role_hidden(client):
    html = client.get("/find").text
    assert '<select id="f-role" name="role">' in html
    assert 'value="tenant" selected' in html
    assert 'data-show-when="role=tenant">' in html  # tenant questions shown
    assert 'data-show-when="role=landlord" hidden disabled' in html
    html = client.get("/find?role=landlord").text
    assert 'data-show-when="role=tenant" hidden disabled' in html
    assert 'value="landlord" selected' in html


def test_find_requires_case_number_and_last_name(session, client):
    resp = client.post("/find", data={**tenant_form(), "role": "tenant"})
    assert resp.status_code == 422 and resp.text.count(FLAGGED) == 2
    assert 'id="err-case_number"' in resp.text and 'id="err-last_name"' in resp.text
    assert _count(session, FallbackSubmission) == 0


def test_find_role_switch_without_javascript_keeps_typing_and_checks_nothing(session, client):
    resp = client.post(
        "/find",
        data={
            "role": "landlord",
            "switch_role": "1",
            "case_number": "97K01-2603-EV-000001",
            "last_name": "Rentals",
            "lang": "en",
        },
    )
    assert resp.status_code == 200 and FLAGGED not in resp.text
    assert 'value="landlord" selected' in resp.text and 'value="Rentals"' in resp.text
    assert 'data-show-when="role=tenant" hidden disabled' in resp.text
    assert _count(session, FallbackSubmission) == 0
