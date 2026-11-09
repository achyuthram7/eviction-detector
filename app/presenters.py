"""Turn stored answers into labelled rows for display (party "done" page and staff case page)."""

from __future__ import annotations

from collections.abc import Callable

from core.models import LandlordPolicy, LandlordResponse, TenantResponse

Row = tuple[str, str]


def _money(value) -> str:
    return "—" if value is None else f"${value:,.2f}".replace(".00", "")


def _yes_no(value, t) -> str:
    return t("yes") if value is True else t("no") if value is False else "—"


def _willing(value, t) -> str:
    return {"yes": t("yes"), "no": t("no"), "unsure": t("unsure")}.get(value or "", "—")


def tenant_rows(resp: TenantResponse, t: Callable[..., str]) -> list[Row]:
    wants = "—"
    if resp.wants == "stay":
        wants = t("opt_stay")
    elif resp.wants == "move":
        wants = t("opt_move")
        if resp.move_time_days:
            wants += f" ({t('days_n', n=resp.move_time_days)})"
    problems = [
        label
        for flag, label in (
            (resp.repair_issues, t("opt_repairs")),
            (resp.retaliation_concern, t("opt_retaliation")),
            (resp.notice_dispute, t("opt_notice")),
            (resp.payment_dispute, t("opt_payment_dispute")),
        )
        if flag
    ]
    consent = [
        label
        for flag, label in (
            (resp.consent_text, t("opt_texts")),
            (resp.consent_call, t("opt_calls")),
        )
        if flag
    ]
    language = {
        "English": t("opt_english"),
        "Spanish": t("opt_spanish"),
        "Other": t("opt_other"),
    }.get(resp.preferred_language or "", "—")
    assistance = t(f"opt_{resp.assistance_status}") if resp.assistance_status else "—"
    return [
        (t("q_believed_owed"), _money(resp.believed_owed)),
        (t("q_pay_30d"), _money(resp.pay_30d)),
        (t("q_pay_monthly"), _money(resp.pay_monthly)),
        (t("q_assistance"), assistance),
        (t("q_assistance_amount"), _money(resp.assistance_amount)),
        (t("q_wants"), wants),
        (t("q_problems"), "; ".join(problems) or t("no")),
        (t("q_repair_details"), resp.repair_details or "—"),
        (t("q_legal_aid"), _yes_no(resp.legal_aid, t)),
        (t("q_mediator"), _willing(resp.willing_mediator, t)),
        (t("q_consent"), "; ".join(consent) or t("no")),
        (t("q_contact_phone"), resp.contact_phone or "—"),
        (t("q_language"), language),
    ]


def _plan_rows(resp: LandlordResponse | LandlordPolicy, t) -> list[Row]:
    waive = [
        label
        for flag, label in (
            (resp.waive_late_fees, t("opt_waive_late")),
            (resp.waive_attorney_fees, t("opt_waive_atty")),
        )
        if flag
    ]
    extend = _yes_no(resp.extend_moveout, t)
    if resp.extend_moveout and resp.extend_moveout_days:
        extend += f" ({t('days_n', n=resp.extend_moveout_days)})"
    goal = {"rent": t("opt_rent"), "possession": t("opt_possession"), "both": t("opt_both")}
    plan = _yes_no(resp.accepts_plan, t)
    if resp.accepts_plan and resp.max_plan_months:
        plan += f" (max {resp.max_plan_months})"
    return [
        (t("q_accepts_plan"), plan),
        (t("q_waive"), "; ".join(waive) or t("no")),
        (t("q_extend"), extend),
        (t("q_goal"), goal.get(resp.goal or "", "—")),
        (t("q_ll_mediator"), _willing(resp.willing_mediator, t)),
    ]


def landlord_rows(resp: LandlordResponse, t: Callable[..., str]) -> list[Row]:
    rows = [
        (t("q_amount_owed"), _money(resp.amount_owed_now)),
        (t("q_fees_included"), _money(resp.fees_included)),
    ]
    if resp.min_to_dismiss is not None or resp.min_to_dismiss_pct is None:
        rows.append((t("q_min_dismiss"), _money(resp.min_to_dismiss)))
    else:
        rows.append((t("q_min_dismiss_pct"), f"{resp.min_to_dismiss_pct:.0f}%"))
    return rows + _plan_rows(resp, t)


def policy_rows(policy: LandlordPolicy, t: Callable[..., str]) -> list[Row]:
    pct = "—" if policy.min_to_dismiss_pct is None else f"{policy.min_to_dismiss_pct:.0f}%"
    return [(t("q_min_dismiss_pct"), pct)] + _plan_rows(policy, t)


def values_from(obj, fields: tuple[str, ...]) -> dict:
    return {name: getattr(obj, name) for name in fields} if obj is not None else {}
