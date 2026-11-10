"""Hard-rule guards: banner on every page, no external URLs, ground truth never read by the app."""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select

from core.constants import BANNER_TEXT
from core.models import Case
from tests.factories import import_rows, login, row

ROOT = Path(__file__).resolve().parents[1]


def test_every_page_shows_the_banner(session, client, simple_model):
    simple_model()
    import_rows(session, [row(1)])
    case = session.scalar(select(Case))
    tenant = next(p for p in case.parties if p.role == "tenant")
    public = [
        "/",
        "/login",
        "/find",
        "/find?role=landlord",
        f"/r/{tenant.token}",
        f"/r/{tenant.token}?lang=es",
        f"/r/{tenant.token}/done",
        "/r/unknown-token",
    ]
    for url in public:
        assert BANNER_TEXT in client.get(url).text, url
    login(client, "admin")
    staff = [
        "/staff/queue",
        f"/staff/cases/{case.id}",
        f"/staff/parties/{tenant.id}/letter",
        "/staff/review",
        "/admin/import",
        "/admin/audit",
        "/admin/policies",
        "/admin/reports",
        "/admin/users",
        "/staff/cases/999999",
    ]
    for url in staff:
        assert BANNER_TEXT in client.get(url).text, url


def test_no_external_urls_in_templates_or_static_files():
    pattern = re.compile(r"(?:https?:)?//[a-z0-9-]+\.[a-z]{2,}", re.I)
    files = list((ROOT / "app" / "templates").rglob("*.html"))
    files += list((ROOT / "app" / "static").rglob("*.*"))
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not pattern.search(text), f"external URL in {path}"


def test_app_and_core_never_reference_ground_truth():
    for folder in ("app", "core"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "ground_truth" not in text, path
            assert "synth.paths" not in text, path


def test_security_headers(client):
    resp = client.get("/")
    assert "default-src 'self'" in resp.headers["content-security-policy"]
    assert resp.headers["referrer-policy"] == "no-referrer"
