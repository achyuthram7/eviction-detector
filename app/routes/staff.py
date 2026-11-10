"""Staff dashboard: call queue, case page, call log, status actions, diversion checklist,
party links (QR + letter) and the fallback review queue.

Restricted (confidential/sealed) cases are hidden from clerks and judges at the query level
and their URLs return 404; facilitators and admins see them with a badge. Every case view is
written to the audit log.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import check_csrf, current_user, require_roles
from app.deps import get_db
from app.i18n import translator
from app.presenters import landlord_rows, tenant_rows
from app.templating import render
from core import clock, pipeline
from core.audit import log
from core.constants import (
    ADMIN,
    CLERK,
    FACILITATOR,
    IN_DIVERSION,
    INVITED,
    LANDLORD,
    NO_SETTLEMENT,
    REFERRED,
    TENANT,
    TERMINAL_STATUSES,
)
from core.diversion import build_checklist
from core.intake import attach_fallback, reject_fallback
from core.models import (
    CallLog,
    Case,
    CaseScore,
    FallbackSubmission,
    FeedChange,
    LandlordResponse,
    Party,
    ReconciliationFlag,
    StatusHistory,
    TenantResponse,
    User,
)
from core.queue import call_list, can_see_restricted, get_visible_case
from core.status import diversion_dates, refresh_status
from core.tokens import party_url, qr_png

router = APIRouter(prefix="/staff")

WORKERS = (CLERK, FACILITATOR, ADMIN)  # judges are read-only
CHANNELS = ("phone", "text", "mail", "email", "in_person")
OUTCOMES = ("reached", "voicemail", "no_answer", "wrong_number", "declined", "scheduled")
STAFF_ACTIONS = {
    "invited": "Mark invitations sent",
    "referred": "Refer to facilitator",
    "no_settlement": "Mark no settlement",
    "clear": "Clear staff status",
}


def _case_or_404(db: Session, case_id: int, user: User) -> Case:
    case = get_visible_case(db, case_id, user.role)
    if case is None:
        if db.get(Case, case_id) is not None:
            log(db, user, "view_denied", details={"case_id": case_id})
            db.commit()
        raise HTTPException(status_code=404, detail="Not found")
    return case


@router.get("")
def staff_home():
    return RedirectResponse("/staff/queue", status_code=303)


@router.get("/queue")
def queue(
    request: Request,
    status: str | None = None,
    potential: str | None = None,
    pathway: str | None = None,
    show_closed: str | None = None,
    q: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = call_list(
        db,
        user.role,
        status=status or None,
        potential=potential or None,
        pathway=pathway or None,
        show_closed=bool(show_closed),
        search=q or None,
    )
    ctx = dict(
        rows=rows,
        filters={
            "status": status or "",
            "potential": potential or "",
            "pathway": pathway or "",
            "show_closed": bool(show_closed),
            "q": q or "",
        },
        sees_restricted=can_see_restricted(user.role),
        today=clock.today(),
    )
    template = "staff/_queue_rows.html" if request.headers.get("HX-Request") else "staff/queue.html"
    return render(request, template, **ctx)


def _case_context(db: Session, case: Case, user: User) -> dict:
    t = translator("en")
    tenants = [p for p in case.parties if p.role == TENANT]
    landlord = next((p for p in case.parties if p.role == LANDLORD), None)
    t_resp = {
        r.party_id: r
        for r in db.scalars(select(TenantResponse).where(TenantResponse.case_id == case.id))
    }
    l_resp = db.scalar(select(LandlordResponse).where(LandlordResponse.case_id == case.id))
    score = db.scalar(
        select(CaseScore).where(CaseScore.case_id == case.id, CaseScore.is_current.is_(True))
    )
    history = db.scalars(
        select(StatusHistory).where(StatusHistory.case_id == case.id).order_by(StatusHistory.at)
    ).all()
    changes = db.scalars(
        select(FeedChange).where(FeedChange.case_id == case.id).order_by(FeedChange.id)
    ).all()
    flags = db.scalars(
        select(ReconciliationFlag)
        .where(ReconciliationFlag.case_id == case.id)
        .order_by(ReconciliationFlag.created_at.desc())
    ).all()
    calls = db.scalars(
        select(CallLog).where(CallLog.case_id == case.id).order_by(CallLog.at.desc())
    ).all()
    users = {u.id: u for u in db.scalars(select(User))}
    days = (case.hearing_datetime.date() - clock.today()).days if case.hearing_datetime else None
    return dict(
        case=case,
        tenants=[
            {
                "party": p,
                "resp": t_resp.get(p.id),
                "rows": tenant_rows(t_resp[p.id], t) if p.id in t_resp else [],
                "url": party_url(p),
            }
            for p in tenants
        ],
        landlord={
            "party": landlord,
            "resp": l_resp,
            "rows": landlord_rows(l_resp, t) if l_resp else [],
            "url": party_url(landlord) if landlord else None,
        },
        score=score,
        history=history,
        changes=changes,
        flags=flags,
        calls=calls,
        users=users,
        days=days,
        can_work=user.role in WORKERS,
        diversion=diversion_dates(case.stay_start_date)
        if case.working_status == IN_DIVERSION
        else None,
        terminal=case.working_status in TERMINAL_STATUSES,
        staff_actions=STAFF_ACTIONS,
        channels=CHANNELS,
        outcomes=OUTCOMES,
        today=clock.today(),
    )


@router.get("/cases/{case_id}")
def case_page(
    request: Request,
    case_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    case = _case_or_404(db, case_id, user)
    log(db, user, "view_case", case=case)
    db.commit()
    return render(request, "staff/case.html", **_case_context(db, case, user))


@router.post("/cases/{case_id}/calls", dependencies=[Depends(check_csrf)])
def add_call(
    request: Request,
    case_id: int,
    channel: str = Form("phone"),
    outcome: str = Form("reached"),
    party_id: str = Form(""),
    notes: str = Form(""),
    next_action: str = Form(""),
    next_action_due: str = Form(""),
    user: User = Depends(require_roles(*WORKERS)),
    db: Session = Depends(get_db),
):
    case = _case_or_404(db, case_id, user)
    party = db.get(Party, int(party_id)) if party_id.isdigit() else None
    if party is not None and party.case_id != case.id:
        party = None
    due = None
    if next_action_due:
        try:
            due = date.fromisoformat(next_action_due)
        except ValueError:
            due = None
    db.add(
        CallLog(
            case_id=case.id,
            party_id=party.id if party else None,
            user_id=user.id,
            at=clock.now(),
            channel=channel if channel in CHANNELS else "phone",
            outcome=outcome if outcome in OUTCOMES else "reached",
            notes=notes.strip()[:2000] or None,
            next_action=next_action.strip()[:200] or None,
            next_action_due=due,
        )
    )
    if case.invited_at is None:
        case.invited_at = clock.now()  # a contact attempt counts as an invitation
        refresh_status(db, case, source="staff", user_id=user.id, note="contact attempt logged")
    log(db, user, "call_logged", case=case, details={"channel": channel, "outcome": outcome})
    db.commit()
    if request.headers.get("HX-Request"):
        ctx = _case_context(db, case, user)
        return render(request, "staff/_call_log.html", **ctx)
    return RedirectResponse(f"/staff/cases/{case.id}#calls", status_code=303)


@router.post("/cases/{case_id}/status", dependencies=[Depends(check_csrf)])
def set_status(
    case_id: int,
    action: str = Form(...),
    user: User = Depends(require_roles(*WORKERS)),
    db: Session = Depends(get_db),
):
    case = _case_or_404(db, case_id, user)
    if case.working_status in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="The official record has closed this case.")
    if action == "invited":
        case.invited_at = case.invited_at or clock.now()
        if case.staff_status == INVITED:
            case.staff_status = None
    elif action == "referred":
        case.staff_status = REFERRED
    elif action == "no_settlement":
        case.staff_status = NO_SETTLEMENT
    elif action == "clear":
        case.staff_status = None
    else:
        raise HTTPException(status_code=400, detail="Unknown action")
    refresh_status(db, case, source="staff", user_id=user.id, note=STAFF_ACTIONS[action])
    pipeline.rescore(db, [case.id])
    log(db, user, "status_change", case=case, details={"action": action})
    db.commit()
    return RedirectResponse(f"/staff/cases/{case.id}", status_code=303)


@router.post("/cases/{case_id}/diversion", dependencies=[Depends(check_csrf)])
def diversion_agreed(
    request: Request,
    case_id: int,
    user: User = Depends(require_roles(*WORKERS)),
    db: Session = Depends(get_db),
):
    """Record (in this tool only) that both parties agreed, and show the Odyssey checklist."""
    case = _case_or_404(db, case_id, user)
    if case.diversion_agreed_at is None:
        case.diversion_agreed_at = clock.now()
    start = case.stay_start_date or case.diversion_agreed_at.date()
    log(db, user, "diversion_marked", case=case)
    db.commit()
    return render(
        request, "staff/_checklist.html", case=case, checklist=build_checklist(case, start)
    )


@router.get("/parties/{party_id}/qr.png")
def party_qr(
    party_id: int, user: User = Depends(require_roles(*WORKERS)), db: Session = Depends(get_db)
):
    party = db.get(Party, party_id)
    if party is None or get_visible_case(db, party.case_id, user.role) is None:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(qr_png(party_url(party)), media_type="image/png")


@router.get("/parties/{party_id}/letter")
def party_letter(
    request: Request,
    party_id: int,
    user: User = Depends(require_roles(*WORKERS)),
    db: Session = Depends(get_db),
):
    party = db.get(Party, party_id)
    if party is None or get_visible_case(db, party.case_id, user.role) is None:
        raise HTTPException(status_code=404, detail="Not found")
    log(db, user, "print_letter", case=party.case, details={"party_id": party.id})
    db.commit()
    return render(
        request,
        "staff/letter.html",
        party=party,
        case=party.case,
        url=party_url(party),
        t_en=translator("en"),
        t_es=translator("es"),
    )


# --- fallback review queue ----------------------------------------------------------------------


@router.get("/review")
def review_queue(
    request: Request, user: User = Depends(require_roles(*WORKERS)), db: Session = Depends(get_db)
):
    subs = db.scalars(
        select(FallbackSubmission)
        .where(FallbackSubmission.status.in_(("review", "matched_unverified")))
        .order_by(FallbackSubmission.created_at.desc())
    ).all()
    items = []
    for sub in subs:
        case = db.scalar(select(Case).where(Case.case_number == sub.case_number_entered))
        visible = case is not None and get_visible_case(db, case.id, user.role) is not None
        items.append(
            {
                "sub": sub,
                "case": case if visible else None,
                "case_exists_hidden": case is not None and not visible,
            }
        )
    return render(request, "staff/review.html", items=items)


@router.post("/review/{sub_id}", dependencies=[Depends(check_csrf)])
def review_action(
    sub_id: int,
    action: str = Form(...),
    party_id: str = Form(""),
    note: str = Form(""),
    user: User = Depends(require_roles(*WORKERS)),
    db: Session = Depends(get_db),
):
    sub = db.get(FallbackSubmission, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Not found")
    if action == "attach" and party_id.isdigit():
        party = db.get(Party, int(party_id))
        if party is None or get_visible_case(db, party.case_id, user.role) is None:
            raise HTTPException(status_code=404, detail="Not found")
        attach_fallback(db, sub, party, user.id)
        pipeline.after_intake(db, [party.case], source="staff", user_id=user.id)
        log(db, user, "fallback_attached", case=party.case, details={"submission": sub.id})
    elif action == "reject":
        reject_fallback(db, sub, user.id, note.strip()[:500] or None)
        log(db, user, "fallback_rejected", details={"submission": sub.id})
    else:
        raise HTTPException(status_code=400, detail="Unknown action")
    db.commit()
    return RedirectResponse("/staff/review", status_code=303)


def _fmt_dt(value: datetime | None) -> str:  # pragma: no cover - template helper
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"
