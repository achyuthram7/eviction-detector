"""Intake details: disclosure placement, language toggle, timings, fallback rules, QR codes."""

from __future__ import annotations

from sqlalchemy import select

from core.constants import DISCLOSURE_TEXT, TENANT
from core.intake import parse_tenant_form, submit_fallback
from core.models import Case, FallbackSubmission, IntakeTiming, TenantResponse
from core.tokens import party_url, qr_png
from tests.factories import import_rows, row, tenant_form


def _tenant(session, case_number="97K01-2603-EV-000001"):
    session.expire_all()
    case = session.scalar(select(Case).where(Case.case_number == case_number))
    return next(p for p in case.parties if p.role == TENANT)


def test_disclosure_shown_before_first_question(session, client):
    import_rows(session, [row(1)])
    html = client.get(f"/r/{_tenant(session).token}").text
    assert DISCLOSURE_TEXT in html
    assert html.index(DISCLOSURE_TEXT) < html.index('name="believed_owed"')


def test_spanish_toggle(session, client):
    import_rows(session, [row(1)])
    html = client.get(f"/r/{_tenant(session).token}?lang=es").text
    assert "El juez y el personal del tribunal pueden ver sus respuestas" in html
    assert "¿Cuánto cree que debe?" in html
    assert "Prototype — synthetic data only. Not legal advice." in html


def test_form_open_and_submit_timestamps_recorded(session, client):
    import_rows(session, [row(1)])
    token = _tenant(session).token
    page = client.get(f"/r/{token}")
    timing_id = page.text.split('name="timing_id" value="')[1].split('"')[0]
    client.post(f"/r/{token}", data={**tenant_form(), "timing_id": timing_id})
    session.expire_all()
    timing = session.get(IntakeTiming, int(timing_id))
    assert timing.opened_at and timing.submitted_at and timing.submitted_at >= timing.opened_at
    assert timing.is_synthetic is False


def test_bad_number_rerenders_with_error_and_saves_nothing(session, client):
    import_rows(session, [row(1)])
    resp = client.post(f"/r/{_tenant(session).token}", data=tenant_form(pay_30d="lots"))
    assert resp.status_code == 422 and "Please enter a number" in resp.text
    assert session.scalars(select(TenantResponse)).all() == []


def test_unknown_token_is_neutral_404(client):
    resp = client.get("/r/not-a-real-token")
    assert resp.status_code == 404 and "Link not found" in resp.text


def test_closed_case_stops_accepting_answers(session, client):
    import_rows(session, [row(1)])
    token = _tenant(session).token
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
    assert client.get(f"/r/{token}").status_code == 410


def test_fallback_with_matching_last_name_attaches_unverified(session):
    import_rows(session, [row(1, defendant_names="Maria De La Cruz")])
    values = parse_tenant_form(tenant_form()).values
    sub, case = submit_fallback(
        session, role="tenant", case_number="97k01-2603-ev-000001", last_name="Cruz", values=values
    )
    session.commit()
    assert sub.status == "matched_unverified" and case is not None
    resp = session.scalar(select(TenantResponse))
    assert resp.source == "fallback" and resp.verified is False


def test_fallback_never_overwrites_answers_given_through_the_link(session, client):
    import_rows(session, [row(1)])
    token = _tenant(session).token
    client.post(f"/r/{token}", data=tenant_form(pay_30d="900"))
    client.post(
        "/find",
        data={
            **tenant_form(pay_30d="5"),
            "role": "tenant",
            "case_number": "97K01-2603-EV-000001",
            "last_name": "Tester1",
        },
    )
    session.expire_all()
    resp = session.scalar(select(TenantResponse))
    assert resp.pay_30d == 900.0 and resp.source == "token"
    sub = session.scalar(select(FallbackSubmission))
    assert sub.status == "matched_unverified" and "kept" in sub.note


def test_landlord_fallback_always_goes_to_review(session, client):
    import_rows(session, [row(1)])
    client.post(
        "/find",
        data={
            "role": "landlord",
            "case_number": "97K01-2603-EV-000001",
            "last_name": "Rentals",
            "min_to_dismiss": "100",
        },
    )
    assert session.scalar(select(FallbackSubmission)).status == "review"


def test_qr_png_encodes_token_link_not_case_number(session):
    import_rows(session, [row(1)])
    party = _tenant(session)
    url = party_url(party)
    assert party.token in url and party.case.case_number not in url
    png = qr_png(url)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 200
