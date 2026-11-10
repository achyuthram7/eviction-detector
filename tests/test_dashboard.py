"""Dashboard behaviour: sign-in, roles, CSRF, call log, statuses, diversion checklist, import."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from core.constants import INVITED, TENANT
from core.models import AuditLog, CallLog, Case, FallbackSubmission, ReconciliationFlag
from core.status import refresh_status
from tests.factories import csv_bytes, import_rows, login, row, tenant_form


def _case(session, n=1) -> Case:
    session.expire_all()
    return session.scalar(select(Case).where(Case.case_number == f"97K01-2603-EV-{n:06d}"))


def test_staff_pages_require_sign_in(client):
    resp = client.get("/staff/queue", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"].startswith("/login")


def test_wrong_password_rejected(client):
    login(client, "clerk")  # creates the demo users
    client.post("/logout")
    resp = client.post("/login", data={"username": "clerk", "password": "nope"})
    assert resp.status_code == 401


def test_csrf_required_for_staff_posts(session, client):
    import_rows(session, [row(1)])
    login(client, "clerk")
    resp = client.post(f"/staff/cases/{_case(session).id}/status", data={"action": "referred"})
    assert resp.status_code == 403


def test_judge_cannot_change_anything(session, client):
    import_rows(session, [row(1)])
    csrf = login(client, "judge")
    resp = client.post(
        f"/staff/cases/{_case(session).id}/calls",
        data={"channel": "phone", "outcome": "reached", "csrf_token": csrf},
    )
    assert resp.status_code == 403


def test_call_log_records_attempt_and_marks_invited(session, client):
    import_rows(session, [row(1)])
    csrf = login(client, "clerk")
    case = _case(session)
    resp = client.post(
        f"/staff/cases/{case.id}/calls",
        headers={"HX-Request": "true", "X-CSRF-Token": csrf},
        data={
            "channel": "phone",
            "outcome": "voicemail",
            "notes": "Left message",
            "next_action": "Call back Friday",
        },
    )
    assert resp.status_code == 200 and "Left message" in resp.text
    assert _case(session).working_status == INVITED
    call = session.scalar(select(CallLog))
    assert call.outcome == "voicemail" and call.user_id is not None
    assert "Call back Friday" in client.get("/staff/queue").text  # next action in the queue


def test_referred_status_then_feed_override_flag(session, client):
    import_rows(session, [row(1)])
    csrf = login(client, "facilitator")
    client.post(
        f"/staff/cases/{_case(session).id}/status", data={"action": "referred", "csrf_token": csrf}
    )
    assert _case(session).working_status == "Referred"
    import_rows(
        session,
        [
            row(
                1,
                latest_event="dismissed",
                disposition_type="dismissed_on_payment",
                disposition_date="2026-03-15",
            )
        ],
    )
    flag = session.scalar(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "feed_override")
    )
    assert flag is not None and "Referred" in flag.message


def test_both_agreed_to_diversion_shows_checklist_and_sends_nothing(session, client):
    import_rows(session, [row(1)])
    csrf = login(client, "facilitator")
    resp = client.post(
        f"/staff/cases/{_case(session).id}/diversion", headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 200
    assert "Odyssey entries to make" in resp.text and "Draft case management order" in resp.text
    assert "April 15, 2026" in resp.text and "May 15, 2026" in resp.text  # +30 / +60 days
    assert "DRAFT" in resp.text and "Nothing was sent" in resp.text
    case = _case(session)
    assert case.diversion_agreed_at is not None
    assert case.working_status != "In diversion"  # only the official feed can set that


def test_unconfirmed_diversion_is_flagged_on_import(session):
    import_rows(session, [row(1)])
    case = _case(session)
    case.diversion_agreed_at = datetime(2026, 3, 1, 10, 0)
    session.commit()
    run = import_rows(session, [row(1)])
    assert run.reconciliation_flags == 1
    assert session.scalar(select(ReconciliationFlag)).kind == "diversion_unconfirmed"


def test_web_import_shows_rejections(session, client):
    csrf = login(client, "clerk")
    data = csv_bytes([row(1), row(2, case_type="SC")])
    resp = client.post(
        "/admin/import", data={"csrf_token": csrf}, files={"file": ("day.csv", data, "text/csv")}
    )
    assert resp.status_code == 200
    assert "case_type is &#39;SC&#39;, expected &#39;EV&#39;" in resp.text
    assert session.scalar(select(AuditLog).where(AuditLog.action == "import")) is not None


def test_review_queue_attach_verifies_submission(session, client):
    import_rows(session, [row(1)])
    client.post(
        "/find",
        data={
            **tenant_form(),
            "role": "tenant",
            "case_number": "97K01-2603-EV-000001",
            "last_name": "Nobody",
        },
    )
    csrf = login(client, "clerk")
    sub = session.scalar(select(FallbackSubmission))
    assert "Nobody" in client.get("/staff/review").text
    party = next(p for p in _case(session).parties if p.role == TENANT)
    client.post(
        f"/staff/review/{sub.id}",
        data={"action": "attach", "party_id": party.id, "csrf_token": csrf},
    )
    session.expire_all()
    assert session.get(FallbackSubmission, sub.id).status == "attached"
    assert _case(session).working_status == "Partial response"


def test_qr_and_letter(session, client):
    import_rows(session, [row(1)])
    login(client, "clerk")
    party = next(p for p in _case(session).parties if p.role == TENANT)
    png = client.get(f"/staff/parties/{party.id}/qr.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    letter = client.get(f"/staff/parties/{party.id}/letter").text
    assert party.token in letter and "The other party cannot." in letter


def test_continuance_resorts_queue_in_web_view(session, client):
    import_rows(
        session,
        [row(1, hearing_datetime="2026-03-18T09:00"), row(2, hearing_datetime="2026-03-20T09:00")],
    )
    login(client, "clerk")
    text = client.get("/staff/queue").text
    assert text.index("EV-000001") < text.index("EV-000002")
    import_rows(
        session,
        [
            row(1, hearing_datetime="2026-04-08T09:00", latest_event="continued"),
            row(2, hearing_datetime="2026-03-20T09:00"),
        ],
    )
    text = client.get("/staff/queue").text
    assert text.index("EV-000002") < text.index("EV-000001")


def test_admin_pages_render_and_are_admin_only(session, client):
    import_rows(session, [row(1)])
    login(client, "admin")
    for url in (
        "/admin/import",
        "/admin/audit",
        "/admin/policies",
        "/admin/reports",
        "/admin/users",
        "/staff/review",
    ):
        assert client.get(url).status_code == 200, url
    login(client, "clerk")
    assert client.get("/admin/audit").status_code == 403


def test_denied_restricted_view_is_logged(session, client):
    import_rows(session, [row(1, sealed_flag="true")])
    login(client, "clerk")
    assert client.get(f"/staff/cases/{_case(session).id}").status_code == 404
    assert session.scalar(select(AuditLog).where(AuditLog.action == "view_denied")) is not None


def test_queue_filter_returns_htmx_partial(session, client):
    import_rows(session, [row(1), row(2)])
    login(client, "clerk")
    case = _case(session, 2)
    case.invited_at = datetime(2026, 3, 10, 10, 0)
    refresh_status(session, case, source="staff")
    session.commit()
    text = client.get("/staff/queue?status=Invited", headers={"HX-Request": "true"}).text
    assert "EV-000002" in text and "EV-000001" not in text and "<html" not in text


def test_import_demo_file_button(session, client):
    from core.settings import get_settings
    from tests.factories import csv_bytes

    demo = get_settings().demo_dir
    demo.mkdir(parents=True, exist_ok=True)
    (demo / "day1.csv").write_bytes(csv_bytes([row(1), row(2)]))
    csrf = login(client, "clerk")
    assert "Import day1.csv" in client.get("/admin/import").text
    resp = client.post("/admin/import/demo/day1.csv", data={"csrf_token": csrf})
    assert resp.status_code == 200 and "2</strong> new cases" in resp.text
    assert (
        client.post("/admin/import/demo/..%2Fapp.db", data={"csrf_token": csrf}).status_code == 404
    )
