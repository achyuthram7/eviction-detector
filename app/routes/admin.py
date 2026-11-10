"""Admin pages: feed import + import report, audit log, policies, reports, users.
Clerks may also import (they handle the daily file)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import check_csrf, require_roles
from app.deps import get_db
from app.templating import render
from core.audit import log
from core.constants import ADMIN, CLERK
from core.importer import import_csv
from core.models import (
    AuditLog,
    Case,
    ImportRun,
    LandlordPolicy,
    Plaintiff,
    ReconciliationFlag,
    RetentionAggregate,
    User,
)
from core.parties import ensure_portfolio_token
from core.queue import can_see_restricted
from core.settings import get_settings
from core.tokens import policy_url

router = APIRouter(prefix="/admin")
IMPORTERS = (CLERK, ADMIN)
MAX_UPLOAD = 20 * 1024 * 1024


@router.get("/import")
def import_page(
    request: Request, user: User = Depends(require_roles(*IMPORTERS)), db: Session = Depends(get_db)
):
    runs = db.scalars(select(ImportRun).order_by(ImportRun.id.desc()).limit(20)).all()
    return render(request, "admin/import.html", runs=runs)


@router.post("/import", dependencies=[Depends(check_csrf)])
async def import_upload(
    file: UploadFile = File(...),
    user: User = Depends(require_roles(*IMPORTERS)),
    db: Session = Depends(get_db),
):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="File too large")
    run = import_csv(db, data, filename=Path(file.filename or "upload.csv").name, user_id=user.id)
    log(db, user, "import", details={"import_id": run.id, "file": run.filename})
    db.commit()
    return RedirectResponse(f"/admin/imports/{run.id}", status_code=303)


@router.get("/imports/{run_id}")
def import_report(
    request: Request,
    run_id: int,
    user: User = Depends(require_roles(*IMPORTERS)),
    db: Session = Depends(get_db),
):
    run = db.get(ImportRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Not found")
    flags = db.scalars(
        select(ReconciliationFlag)
        .where(ReconciliationFlag.import_id == run.id)
        .order_by(ReconciliationFlag.id)
    ).all()
    cases = {
        c.id: c for c in db.scalars(select(Case).where(Case.id.in_([f.case_id for f in flags])))
    }
    show_restricted = can_see_restricted(user.role)
    visible_flags = [
        (f, cases.get(f.case_id))
        for f in flags
        if show_restricted or not cases.get(f.case_id).is_restricted
    ]
    hidden = len(flags) - len(visible_flags)
    return render(
        request, "admin/import_report.html", run=run, flags=visible_flags, hidden_flags=hidden
    )


@router.get("/audit")
def audit_log(
    request: Request,
    case_id: int | None = None,
    restricted: str | None = None,
    user: User = Depends(require_roles(ADMIN)),
    db: Session = Depends(get_db),
):
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(500)
    if case_id:
        q = q.where(AuditLog.case_id == case_id)
    if restricted:
        q = q.where(AuditLog.restricted.is_(True))
    entries = db.scalars(q).all()
    numbers = dict(
        db.execute(
            select(Case.id, Case.case_number).where(
                Case.id.in_({e.case_id for e in entries if e.case_id})
            )
        ).all()
    )
    return render(
        request, "admin/audit.html", entries=entries, numbers=numbers, restricted=bool(restricted)
    )


@router.get("/policies")
def policies(
    request: Request, user: User = Depends(require_roles(ADMIN)), db: Session = Depends(get_db)
):
    counts = dict(
        db.execute(select(Case.plaintiff_id, func.count(Case.id)).group_by(Case.plaintiff_id)).all()
    )
    plaintiffs = sorted(db.scalars(select(Plaintiff)), key=lambda p: -counts.get(p.plaintiff_id, 0))
    for p in plaintiffs[:50]:
        ensure_portfolio_token(db, p)
    db.commit()
    policies = {p.plaintiff_id: p for p in db.scalars(select(LandlordPolicy))}
    rows = [
        {
            "plaintiff": p,
            "cases": counts.get(p.plaintiff_id, 0),
            "policy": policies.get(p.plaintiff_id),
            "url": policy_url(p),
        }
        for p in plaintiffs[:50]
    ]
    return render(request, "admin/policies.html", rows=rows)


@router.get("/reports")
def reports(
    request: Request,
    name: str | None = None,
    user: User = Depends(require_roles(ADMIN)),
    db: Session = Depends(get_db),
):
    folder = get_settings().reports_dir
    files = sorted(p.name for p in folder.glob("*.md")) if folder.exists() else []
    chosen = name if name in files else ("summary.md" if "summary.md" in files else None)
    text = (folder / chosen).read_text(encoding="utf-8") if chosen else None
    aggregates = db.scalars(
        select(RetentionAggregate).order_by(RetentionAggregate.month.desc()).limit(50)
    ).all()
    return render(
        request, "admin/reports.html", files=files, chosen=chosen, text=text, aggregates=aggregates
    )


@router.get("/reports/file/{path:path}")
def report_file(path: str, user: User = Depends(require_roles(ADMIN))):
    folder = get_settings().reports_dir.resolve()
    target = (folder / path).resolve()
    if (
        folder not in target.parents
        or not target.is_file()
        or target.suffix not in (".png", ".md", ".csv", ".html")
    ):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)


@router.get("/users")
def users(
    request: Request, user: User = Depends(require_roles(ADMIN)), db: Session = Depends(get_db)
):
    return render(request, "admin/users.html", users=db.scalars(select(User)).all())


@router.get("")
def admin_home():
    return RedirectResponse("/admin/import", status_code=303)
