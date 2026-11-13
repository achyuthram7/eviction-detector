"""Party-facing pages: token intake, fallback lookup, landlord standing policy.

A token identifies exactly one party on one case. These routes only ever load that party's own
answers — never the other party's (hard rule 3).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.deps import get_db
from app.i18n import pick_lang, translator
from app.presenters import landlord_rows, policy_rows, tenant_rows, values_from
from app.templating import render
from core import pipeline
from core.constants import LANDLORD, TENANT
from core.intake import (
    LANDLORD_FIELDS,
    POLICY_FIELDS,
    TENANT_FIELDS,
    accepting_answers,
    close_timing,
    get_landlord_response,
    get_tenant_response,
    open_timing,
    parse_landlord_form,
    parse_policy_form,
    parse_tenant_form,
    party_for_token,
    plaintiff_for_token,
    save_landlord_response,
    save_policy,
    save_tenant_response,
    submit_fallback,
    unsure_answers,
)
from core.models import LandlordPolicy

router = APIRouter()


def _not_found(request: Request, lang: str):
    return render(
        request,
        "public/message.html",
        status_code=404,
        lang=lang,
        title_key="nf_title",
        body_key="nf_body",
        show_find=True,
    )


def _closed(request: Request, lang: str):
    return render(
        request,
        "public/message.html",
        status_code=410,
        lang=lang,
        title_key="closed_title",
        body_key="closed_body",
        show_find=False,
    )


@router.get("/")
def index(request: Request):
    return render(request, "index.html")


# --- token intake -----------------------------------------------------------------------------


def _form_context(party, case, lang, values, errors, timing_id, existing, unsure=frozenset()):
    return dict(
        lang=lang,
        party=party,
        case=case,
        values=values,
        errors=errors,
        unsure=unsure,
        timing_id=timing_id,
        existing=existing,
        action=f"/r/{party.token}",
        toggle_url=f"/r/{party.token}?lang={translator(lang)('lang_toggle_code')}",
    )


@router.get("/r/{token}")
def intake_form(
    request: Request, token: str, lang: str | None = None, db: Session = Depends(get_db)
):
    lang = pick_lang(lang)
    party = party_for_token(db, token)
    if party is None:
        return _not_found(request, lang)
    case = party.case
    if not accepting_answers(case):
        return _closed(request, lang)
    timing = open_timing(db, role=party.role, party=party, language=lang)
    db.commit()
    if party.role == TENANT:
        existing = get_tenant_response(db, party)
        values, template = values_from(existing, TENANT_FIELDS), "public/intake_tenant.html"
    else:
        existing = get_landlord_response(db, party)
        if existing is not None and existing.source == "policy":
            existing = None  # a case-specific form starts blank; the policy still applies
        values, template = values_from(existing, LANDLORD_FIELDS), "public/intake_landlord.html"
    unsure = unsure_answers(values, party.role)  # re-opened answers show "Not sure" again
    return render(
        request,
        template,
        **_form_context(party, case, lang, values, {}, timing.id, existing, unsure),
    )


@router.post("/r/{token}")
async def intake_submit(request: Request, token: str, db: Session = Depends(get_db)):
    form = await request.form()
    lang = pick_lang(form.get("lang"))
    party = party_for_token(db, token)
    if party is None:
        return _not_found(request, lang)
    case = party.case
    if not accepting_answers(case):
        return _closed(request, lang)
    parsed = parse_tenant_form(form) if party.role == TENANT else parse_landlord_form(form)
    if not parsed.ok:
        template = (
            "public/intake_tenant.html" if party.role == TENANT else "public/intake_landlord.html"
        )
        ctx = _form_context(
            party,
            case,
            lang,
            parsed.values,
            parsed.errors,
            form.get("timing_id"),
            None,
            parsed.unsure,
        )
        return render(request, template, status_code=422, **ctx)
    timing = close_timing(db, form.get("timing_id"), party=party)
    started = timing.opened_at if timing else None
    submitted = timing.submitted_at if timing else None
    save = save_tenant_response if party.role == TENANT else save_landlord_response
    save(db, party, parsed.values, started_at=started, submitted_at=submitted, language=lang)
    pipeline.after_intake(db, [case], source="system")
    db.commit()
    return RedirectResponse(f"/r/{token}/done?lang={lang}", status_code=303)


@router.get("/r/{token}/done")
def intake_done(
    request: Request, token: str, lang: str | None = None, db: Session = Depends(get_db)
):
    lang = pick_lang(lang)
    party = party_for_token(db, token)
    if party is None:
        return _not_found(request, lang)
    t = translator(lang)
    if party.role == TENANT:
        resp = get_tenant_response(db, party)
        rows = tenant_rows(resp, t) if resp else []
    else:
        resp = get_landlord_response(db, party)
        rows = landlord_rows(resp, t) if resp else []
    return render(
        request,
        "public/done.html",
        lang=lang,
        rows=rows,
        resp=resp,
        edit_url=f"/r/{token}?lang={lang}",
        toggle_url=f"/r/{token}/done?lang={t('lang_toggle_code')}",
    )


# --- fallback: case number + last name ----------------------------------------------------------


@router.get("/find")
def find_form(
    request: Request,
    lang: str | None = None,
    role: str | None = None,
    db: Session = Depends(get_db),
):
    lang = pick_lang(lang)
    role = role if role in (TENANT, LANDLORD) else TENANT
    timing = open_timing(db, role=role, kind="fallback", language=lang)
    db.commit()
    t = translator(lang)
    return render(
        request,
        "public/find.html",
        lang=lang,
        role=role,
        values={},
        errors={},
        unsure=set(),
        entered={},
        timing_id=timing.id,
        toggle_url=f"/find?lang={t('lang_toggle_code')}&role={role}",
    )


@router.post("/find")
async def find_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    lang = pick_lang(form.get("lang"))
    role = form.get("role") if form.get("role") in (TENANT, LANDLORD) else TENANT
    parsed = parse_tenant_form(form) if role == TENANT else parse_landlord_form(form)
    entered = {
        "case_number": (form.get("case_number") or "").strip(),
        "last_name": (form.get("last_name") or "").strip(),
    }
    switching = bool(form.get("switch_role"))  # no-JavaScript role switch: just reload
    for name, value in entered.items():
        if not value:
            parsed.errors[name] = "err_required"
    if switching or not parsed.ok:
        t = translator(lang)
        return render(
            request,
            "public/find.html",
            status_code=200 if switching else 422,
            lang=lang,
            role=role,
            values=parsed.values,
            errors={} if switching else parsed.errors,
            unsure=parsed.unsure,
            entered=entered,
            timing_id=form.get("timing_id"),
            toggle_url=f"/find?lang={t('lang_toggle_code')}&role={role}",
        )
    _sub, matched_case = submit_fallback(
        db,
        role=role,
        case_number=entered["case_number"],
        last_name=entered["last_name"],
        values=parsed.values,
        language=lang,
    )
    close_timing(db, form.get("timing_id"), kind="fallback")
    if matched_case is not None:
        pipeline.after_intake(db, [matched_case], source="system")
    db.commit()
    # Same neutral page whatever happened: never reveal whether the case or name matched.
    return render(
        request,
        "public/message.html",
        lang=lang,
        title_key="find_done_title",
        body_key="find_done_body",
        show_find=False,
    )


# --- landlord standing policy -------------------------------------------------------------------


@router.get("/policy/{token}")
def policy_form(
    request: Request, token: str, lang: str | None = None, db: Session = Depends(get_db)
):
    lang = pick_lang(lang)
    plaintiff = plaintiff_for_token(db, token)
    if plaintiff is None:
        return _not_found(request, lang)
    timing = open_timing(
        db, role=LANDLORD, kind="policy", plaintiff_id=plaintiff.plaintiff_id, language=lang
    )
    db.commit()
    policy = db.get(LandlordPolicy, plaintiff.policy.id) if plaintiff.policy else None
    values = values_from(policy, POLICY_FIELDS)
    t = translator(lang)
    return render(
        request,
        "public/policy.html",
        lang=lang,
        plaintiff=plaintiff,
        values=values,
        errors={},
        unsure=unsure_answers(values, "policy"),
        timing_id=timing.id,
        existing=policy,
        action=f"/policy/{token}",
        toggle_url=f"/policy/{token}?lang={t('lang_toggle_code')}",
    )


@router.post("/policy/{token}")
async def policy_submit(request: Request, token: str, db: Session = Depends(get_db)):
    form = await request.form()
    lang = pick_lang(form.get("lang"))
    plaintiff = plaintiff_for_token(db, token)
    if plaintiff is None:
        return _not_found(request, lang)
    parsed = parse_policy_form(form)
    t = translator(lang)
    if not parsed.ok:
        return render(
            request,
            "public/policy.html",
            status_code=422,
            lang=lang,
            plaintiff=plaintiff,
            values=parsed.values,
            errors=parsed.errors,
            unsure=parsed.unsure,
            timing_id=form.get("timing_id"),
            existing=None,
            action=f"/policy/{token}",
            toggle_url=f"/policy/{token}?lang={t('lang_toggle_code')}",
        )
    timing = close_timing(
        db, form.get("timing_id"), plaintiff_id=plaintiff.plaintiff_id, kind="policy"
    )
    policy, affected = save_policy(
        db,
        plaintiff,
        parsed.values,
        started_at=timing.opened_at if timing else None,
        submitted_at=timing.submitted_at if timing else None,
        language=lang,
    )
    pipeline.after_intake(db, affected, source="system")
    db.commit()
    return render(
        request,
        "public/done.html",
        lang=lang,
        rows=policy_rows(policy, t),
        resp=policy,
        edit_url=f"/policy/{token}?lang={lang}",
        toggle_url=f"/policy/{token}?lang={t('lang_toggle_code')}",
        extra_note=t("policy_applies"),
    )
