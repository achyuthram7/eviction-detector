"""Retention: `make purge` deletes intake details N days after a case closes, keeping counts."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from core import clock
from core.audit import log
from core.constants import TERMINAL_STATUSES
from core.models import (
    CallLog,
    Case,
    CaseScore,
    FallbackSubmission,
    IntakeTiming,
    LandlordResponse,
    Party,
    RetentionAggregate,
    TenantResponse,
)
from core.settings import get_settings


def purge(session: Session, days: int | None = None) -> dict:
    days = get_settings().app["retention_days"] if days is None else days
    today = clock.today()
    cutoff = today - timedelta(days=days)
    now = clock.now()

    candidates = [
        c
        for c in session.scalars(
            select(Case).where(Case.working_status.in_(TERMINAL_STATUSES), Case.purged_at.is_(None))
        )
        if (c.disposition_date or c.updated_at.date()) <= cutoff
    ]
    if not candidates:
        return {"cutoff": cutoff.isoformat(), "cases_purged": 0}

    ids = [c.id for c in candidates]
    scores = {}
    tenant_counts: dict[int, int] = defaultdict(int)
    landlord_ids: set[int] = set()
    party_case: dict[int, int] = {}
    intake_secs: dict[int, list[float]] = defaultdict(list)
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        for s in session.scalars(
            select(CaseScore).where(CaseScore.case_id.in_(chunk), CaseScore.is_current.is_(True))
        ):
            scores[s.case_id] = s
        for (cid,) in session.execute(
            select(TenantResponse.case_id).where(TenantResponse.case_id.in_(chunk))
        ):
            tenant_counts[cid] += 1
        landlord_ids.update(
            session.scalars(
                select(LandlordResponse.case_id).where(LandlordResponse.case_id.in_(chunk))
            )
        )
        for pid, cid in session.execute(
            select(Party.id, Party.case_id).where(Party.case_id.in_(chunk))
        ):
            party_case[pid] = cid
    pids = list(party_case)
    for i in range(0, len(pids), 500):
        for t in session.scalars(
            select(IntakeTiming).where(
                IntakeTiming.party_id.in_(pids[i : i + 500]), IntakeTiming.submitted_at.is_not(None)
            )
        ):
            intake_secs[party_case[t.party_id]].append(
                (t.submitted_at - t.opened_at).total_seconds()
            )

    buckets: dict[tuple, dict] = defaultdict(
        lambda: {"n": 0, "t": 0, "l": 0, "secs": 0.0, "count": 0}
    )
    for c in candidates:
        closed = c.disposition_date or c.updated_at.date()
        score = scores.get(c.id)
        key = (
            closed.strftime("%Y-%m"),
            c.court_id,
            score.potential if score else None,
            score.pathway if score else None,
            c.working_status,
        )
        b = buckets[key]
        b["n"] += 1
        b["t"] += tenant_counts.get(c.id, 0)
        b["l"] += 1 if c.id in landlord_ids else 0
        b["secs"] += sum(intake_secs.get(c.id, []))
        b["count"] += len(intake_secs.get(c.id, []))
    for (month, court, potential, pathway, outcome), b in buckets.items():
        agg = session.scalar(
            select(RetentionAggregate).where(
                RetentionAggregate.month == month,
                RetentionAggregate.court_id == court,
                RetentionAggregate.potential.is_(potential)
                if potential is None
                else RetentionAggregate.potential == potential,
                RetentionAggregate.pathway.is_(pathway)
                if pathway is None
                else RetentionAggregate.pathway == pathway,
                RetentionAggregate.outcome_status == outcome,
            )
        )
        if agg is None:
            agg = RetentionAggregate(
                month=month,
                court_id=court,
                potential=potential,
                pathway=pathway,
                outcome_status=outcome,
                n_cases=0,
                n_tenant_responses=0,
                n_landlord_responses=0,
                intake_seconds_total=0.0,
                intake_count=0,
                purged_at=now,
            )
            session.add(agg)
        agg.n_cases += b["n"]
        agg.n_tenant_responses += b["t"]
        agg.n_landlord_responses += b["l"]
        agg.intake_seconds_total += b["secs"]
        agg.intake_count += b["count"]
        agg.purged_at = now

    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        session.execute(delete(TenantResponse).where(TenantResponse.case_id.in_(chunk)))
        session.execute(delete(LandlordResponse).where(LandlordResponse.case_id.in_(chunk)))
        session.execute(
            update(CallLog).where(CallLog.case_id.in_(chunk)).values(notes=None, next_action=None)
        )
        session.execute(
            update(Party).where(Party.case_id.in_(chunk)).values(phone=None, address_norm=None)
        )
        session.execute(update(Case).where(Case.id.in_(chunk)).values(phone=None, purged_at=now))
    for i in range(0, len(pids), 500):
        chunk = pids[i : i + 500]
        session.execute(delete(IntakeTiming).where(IntakeTiming.party_id.in_(chunk)))
        session.execute(
            update(FallbackSubmission)
            .where(FallbackSubmission.matched_party_id.in_(chunk))
            .values(answers={}, note="purged")
        )
    log(session, None, "purge", details={"cutoff": cutoff.isoformat(), "cases": len(ids)})
    session.flush()
    return {"cutoff": cutoff.isoformat(), "cases_purged": len(ids), "buckets": len(buckets)}
