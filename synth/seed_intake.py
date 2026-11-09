"""Load the generator's intake answers, policies, fallbacks, invitations and call logs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core import pipeline
from core.constants import LANDLORD, TENANT
from core.db import session_scope
from core.intake import save_landlord_response, save_policy, save_tenant_response, submit_fallback
from core.models import CallLog, Case, IntakeTiming, Plaintiff, User
from core.parties import ensure_portfolio_token


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def apply_seed_payload(payload: dict) -> None:
    with session_scope() as session:
        numbers = {i["case_number"] for i in payload["intakes"]}
        numbers |= set(payload["invited"]) | {c["case_number"] for c in payload["call_logs"]}
        cases = {
            c.case_number: c
            for c in session.scalars(
                select(Case)
                .where(Case.case_number.in_(numbers))
                .options(selectinload(Case.parties))
            )
        }
        touched: dict[int, Case] = {}

        for number, when in payload["invited"].items():
            if number in cases:
                cases[number].invited_at = _dt(when)
                touched[cases[number].id] = cases[number]

        # Every plaintiff gets a portfolio link; some already have a standing policy.
        for plaintiff in session.scalars(select(Plaintiff)):
            ensure_portfolio_token(session, plaintiff)
        for pol in payload["policies"]:
            plaintiff = session.get(Plaintiff, pol["plaintiff_id"])
            if plaintiff is None:
                continue
            answers = dict(pol["answers"])
            lang = answers.pop("language", "en")
            opened, submitted = _dt(pol["opened_at"]), _dt(pol["submitted_at"])
            _policy, affected = save_policy(
                session,
                plaintiff,
                answers,
                started_at=opened,
                submitted_at=submitted,
                language=lang,
            )
            session.add(
                IntakeTiming(
                    plaintiff_id=plaintiff.plaintiff_id,
                    role=LANDLORD,
                    kind="policy",
                    language=lang,
                    opened_at=opened,
                    submitted_at=submitted,
                    is_synthetic=True,
                )
            )
            for case in affected:
                touched[case.id] = case

        for item in payload["intakes"]:
            case = cases.get(item["case_number"])
            if case is None:
                continue
            role = item["role"]
            party = next(
                (
                    p
                    for p in case.parties
                    if p.role == role and (role == LANDLORD or p.party_index == item["party_index"])
                ),
                None,
            )
            if party is None:
                continue
            answers = dict(item["answers"])
            lang = answers.pop("language", "en")
            timings = [(_dt(a), _dt(b)) for a, b in item["timings"]]
            for opened, submitted in timings:
                session.add(
                    IntakeTiming(
                        party_id=party.id,
                        role=role,
                        kind="case",
                        language=lang,
                        opened_at=opened,
                        submitted_at=submitted,
                        is_synthetic=True,
                    )
                )
                save = save_tenant_response if role == TENANT else save_landlord_response
                save(
                    session,
                    party,
                    answers,
                    started_at=opened,
                    submitted_at=submitted,
                    language=lang,
                )
            touched[case.id] = case

        for fb in payload["fallbacks"]:
            sub, matched = submit_fallback(
                session,
                role=fb["role_claimed"],
                case_number=fb["case_number_entered"],
                last_name=fb["last_name_entered"],
                values=fb["answers"],
            )
            sub.created_at = _dt(fb["created_at"])
            if matched is not None:
                touched[matched.id] = matched

        clerk = session.scalar(select(User).where(User.username == "clerk"))
        for call in payload["call_logs"]:
            case = cases.get(call["case_number"])
            if case is None:
                continue
            session.add(
                CallLog(
                    case_id=case.id,
                    user_id=clerk.id if clerk else None,
                    at=_dt(call["at"]),
                    channel=call["channel"],
                    outcome=call["outcome"],
                    notes=call["notes"],
                    next_action=call["next_action"],
                )
            )
        session.flush()
        pipeline.after_intake(session, list(touched.values()), source="system")
