"""Staff sign-in, role guards and CSRF protection for state-changing staff requests."""

from __future__ import annotations

import hmac
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_db
from app.templating import render
from core.audit import log
from core.models import User
from core.security import verify_password

router = APIRouter()


def _redirect_to_login(request: Request) -> HTTPException:
    target = quote(str(request.url.path))
    return HTTPException(status_code=303, headers={"Location": f"/login?next={target}"})


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.active:
        raise _redirect_to_login(request)
    request.state.user = user
    return user


def require_roles(*roles: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Your role cannot do this.")
        return user

    return dependency


async def check_csrf(request: Request) -> None:
    expected = request.session.get("csrf")
    sent = request.headers.get("X-CSRF-Token")
    if not sent:
        form = await request.form()
        sent = form.get("csrf_token")
    if not expected or not sent or not hmac.compare_digest(str(sent), str(expected)):
        raise HTTPException(status_code=403, detail="Session expired. Reload the page and retry.")


def _safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/staff/queue"


@router.get("/login")
def login_form(request: Request, next: str | None = None):
    return render(request, "login.html", next=_safe_next(next), error=None)


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/staff/queue"),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    if user is None or not user.active or not verify_password(password, user.password_hash):
        return render(
            request,
            "login.html",
            status_code=401,
            next=_safe_next(next),
            error="Wrong username or password.",
        )
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["csrf"] = secrets.token_urlsafe(16)
    log(db, user, "login")
    db.commit()
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)
