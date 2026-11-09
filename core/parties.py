"""Parties per case: one row per tenant (defendant) plus one landlord row, each with a token."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import clock
from core.constants import LANDLORD, TENANT
from core.models import Case, Party, Plaintiff
from core.normalize import last_name_norm, norm_address, split_list
from core.security import new_token


def _unique_token(session: Session) -> str:
    while True:
        token = new_token()
        exists = session.scalar(select(Party.id).where(Party.token == token))
        if exists is None:
            return token


def ensure_parties(session: Session, case: Case) -> list[Party]:
    """Create missing tenant/landlord parties (and their tokens). Never deletes or renames."""
    now = clock.now()
    existing = list(case.parties)
    tenants = {p.party_index: p for p in existing if p.role == TENANT}
    names = split_list(case.defendant_names)
    phones = split_list(case.phone)
    created: list[Party] = []
    for idx, name in enumerate(names, start=1):
        if idx in tenants:
            continue
        party = Party(
            case=case,
            role=TENANT,
            party_index=idx,
            full_name=name,
            last_name_norm=last_name_norm(name),
            phone=(phones[idx - 1] or None) if idx - 1 < len(phones) else None,
            address_norm=norm_address(case.mailing_address or ""),
            token=_unique_token(session),
            token_created_at=now,
        )
        session.add(party)
        created.append(party)
    if not any(p.role == LANDLORD for p in existing):
        label = session.get(Plaintiff, case.plaintiff_id)
        party = Party(
            case=case,
            role=LANDLORD,
            party_index=0,
            full_name=label.label if label else None,
            token=_unique_token(session),
            token_created_at=now,
        )
        session.add(party)
        created.append(party)
    if created:
        session.flush()
    return created


def ensure_portfolio_token(session: Session, plaintiff: Plaintiff) -> str:
    if not plaintiff.portfolio_token:
        while True:
            token = new_token()
            if session.scalar(select(Plaintiff).where(Plaintiff.portfolio_token == token)) is None:
                break
        plaintiff.portfolio_token = token
        session.flush()
    return plaintiff.portfolio_token


def tenant_parties(case: Case) -> list[Party]:
    return [p for p in case.parties if p.role == TENANT]


def landlord_party(case: Case) -> Party | None:
    return next((p for p in case.parties if p.role == LANDLORD), None)
