"""Jinja2 setup shared by all routes. Every page extends base.html, which renders the banner."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.i18n import translator
from core import constants
from core.status import diversion_dates

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def _money(value) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}".replace(".00", "")


def _pct(value) -> str:
    return "—" if value is None else f"{value:.0%}"


templates.env.filters["money"] = _money
templates.env.filters["pct"] = _pct
templates.env.globals.update(
    BANNER_TEXT=constants.BANNER_TEXT,
    DISCLOSURE_TEXT=constants.DISCLOSURE_TEXT,
    STATUSES=constants.STATUSES,
    POTENTIALS=constants.POTENTIALS,
    PATHWAYS=constants.PATHWAYS,
    diversion_dates=diversion_dates,
)


def render(request: Request, name: str, *, status_code: int = 200, **context):
    lang = context.setdefault("lang", "en")
    context.setdefault("t", translator(lang))
    context.setdefault("user", getattr(request.state, "user", None))
    context.setdefault(
        "csrf_token", request.session.get("csrf") if "session" in request.scope else None
    )
    return templates.TemplateResponse(request, name, context, status_code=status_code)
