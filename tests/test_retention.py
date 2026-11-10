"""`make purge`: intake details deleted N days after closure; aggregate counts kept."""

from __future__ import annotations

from sqlalchemy import select

from core.models import AuditLog, Case, LandlordResponse, RetentionAggregate, TenantResponse
from core.retention import purge
from tests.factories import import_rows, landlord_form, row, tenant_form


def test_purge_removes_answers_and_keeps_counts(session, client):
    old = {"filing_date": "2025-12-01", "hearing_datetime": "2025-12-29T09:00"}
    import_rows(session, [row(1, **old), row(2)])
    case = session.scalar(select(Case).where(Case.case_number.endswith("000001")))
    tenant = next(p for p in case.parties if p.role == "tenant")
    landlord = next(p for p in case.parties if p.role == "landlord")
    client.post(f"/r/{tenant.token}", data=tenant_form())
    client.post(f"/r/{landlord.token}", data=landlord_form())
    import_rows(
        session,
        [
            row(
                1,
                **old,
                latest_event="agreed_judgment",
                disposition_type="agreed_pay_and_stay",
                disposition_date="2026-01-05",
            ),
            row(2),
        ],
    )
    result = purge(session, days=30)
    session.commit()
    assert result["cases_purged"] == 1
    assert session.scalars(select(TenantResponse)).all() == []
    assert session.scalars(select(LandlordResponse)).all() == []
    agg = session.scalar(select(RetentionAggregate))
    assert (agg.n_cases, agg.n_tenant_responses, agg.n_landlord_responses) == (1, 1, 1)
    assert agg.outcome_status == "Settled" and agg.month == "2026-01"
    session.expire_all()
    assert session.get(Case, case.id).purged_at is not None
    assert session.scalar(select(AuditLog).where(AuditLog.action == "purge")) is not None
    assert purge(session, days=30)["cases_purged"] == 0  # idempotent; open case untouched


def test_recent_closures_are_kept(session):
    import_rows(
        session,
        [
            row(
                1,
                latest_event="dismissed",
                disposition_type="dismissed_other",
                disposition_date="2026-03-10",
            )
        ],
    )
    assert purge(session, days=30)["cases_purged"] == 0
