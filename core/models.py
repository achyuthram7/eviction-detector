"""SQLAlchemy ORM models — the whole schema in one file.

Ground truth from the synthetic generator is deliberately NOT stored here: it lives in a
separate file that only the evaluation scripts read.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(default=True)


class Plaintiff(Base):
    __tablename__ = "plaintiffs"
    plaintiff_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(200))
    portfolio_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    policy: Mapped[LandlordPolicy | None] = relationship(back_populates="plaintiff")


class LandlordPolicy(Base):
    """Standing policy: one set of landlord answers applied to every case from a plaintiff."""

    __tablename__ = "landlord_policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    plaintiff_id: Mapped[str] = mapped_column(ForeignKey("plaintiffs.plaintiff_id"), unique=True)
    accepts_plan: Mapped[bool | None]
    max_plan_months: Mapped[int | None]
    min_to_dismiss_pct: Mapped[float | None]  # % of the amount owed
    waive_late_fees: Mapped[bool | None]
    waive_attorney_fees: Mapped[bool | None]
    extend_moveout: Mapped[bool | None]
    extend_moveout_days: Mapped[int | None]
    goal: Mapped[str | None] = mapped_column(String(20))  # rent / possession / both
    willing_mediator: Mapped[str | None] = mapped_column(String(10))  # yes / no / unsure
    language: Mapped[str] = mapped_column(String(5), default="en")
    started_at: Mapped[datetime | None]
    submitted_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None]
    revision: Mapped[int] = mapped_column(default=1)
    plaintiff: Mapped[Plaintiff] = relationship(back_populates="policy")


class ImportRun(Base):
    __tablename__ = "import_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64))
    export_date: Mapped[date | None]
    imported_at: Mapped[datetime]
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    rows_total: Mapped[int] = mapped_column(default=0)
    inserted: Mapped[int] = mapped_column(default=0)
    updated: Mapped[int] = mapped_column(default=0)
    unchanged: Mapped[int] = mapped_column(default=0)
    rejected: Mapped[int] = mapped_column(default=0)
    newly_restricted: Mapped[int] = mapped_column(default=0)
    reconciliation_flags: Mapped[int] = mapped_column(default=0)
    file_error: Mapped[str | None] = mapped_column(Text)
    rejections: Mapped[list[ImportRejection]] = relationship(
        back_populates="run", order_by="ImportRejection.row_number"
    )


class ImportRejection(Base):
    __tablename__ = "import_rejections"
    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("import_runs.id"), index=True)
    row_number: Mapped[int]
    case_number_raw: Mapped[str | None] = mapped_column(String(80))
    reasons: Mapped[list] = mapped_column(JSON)
    run: Mapped[ImportRun] = relationship(back_populates="rejections")


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    court_id: Mapped[str] = mapped_column(String(20), index=True)
    case_type: Mapped[str] = mapped_column(String(5))
    filing_date: Mapped[date] = mapped_column(index=True)
    amount_claimed: Mapped[float | None]
    amount_is_placeholder: Mapped[bool] = mapped_column(default=False)
    first_hearing_datetime: Mapped[datetime | None]  # as-of rule: never updated by continuances
    hearing_datetime: Mapped[datetime | None]
    plaintiff_id: Mapped[str] = mapped_column(ForeignKey("plaintiffs.plaintiff_id"), index=True)
    plaintiff_attorney: Mapped[bool] = mapped_column(default=False)
    defendant_count: Mapped[int] = mapped_column(default=1)
    defendant_names: Mapped[str] = mapped_column(Text)
    mailing_address: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    latest_event: Mapped[str] = mapped_column(String(30))
    latest_event_date: Mapped[date | None]
    disposition_type: Mapped[str | None] = mapped_column(String(40))
    disposition_date: Mapped[date | None] = mapped_column(index=True)
    confidential_flag: Mapped[bool] = mapped_column(default=False)
    sealed_flag: Mapped[bool] = mapped_column(default=False)
    is_restricted: Mapped[bool] = mapped_column(default=False, index=True)
    working_status: Mapped[str] = mapped_column(String(30), default="New", index=True)
    settled_subtype: Mapped[str | None] = mapped_column(String(40))
    staff_status: Mapped[str | None] = mapped_column(String(30))
    stay_start_date: Mapped[date | None]
    invited_at: Mapped[datetime | None]
    diversion_agreed_at: Mapped[datetime | None]
    row_hash: Mapped[str] = mapped_column(String(64))
    first_import_id: Mapped[int | None] = mapped_column(ForeignKey("import_runs.id"))
    last_import_id: Mapped[int | None] = mapped_column(ForeignKey("import_runs.id"))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    purged_at: Mapped[datetime | None]

    plaintiff: Mapped[Plaintiff] = relationship()
    parties: Mapped[list[Party]] = relationship(back_populates="case", order_by="Party.id")


class Party(Base):
    __tablename__ = "parties"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # tenant / landlord
    party_index: Mapped[int] = mapped_column(default=0)
    full_name: Mapped[str | None] = mapped_column(String(200))
    last_name_norm: Mapped[str | None] = mapped_column(String(80), index=True)
    phone: Mapped[str | None] = mapped_column(String(40))
    address_norm: Mapped[str | None] = mapped_column(String(300))
    token: Mapped[str | None] = mapped_column(String(64), unique=True)
    token_created_at: Mapped[datetime | None]
    token_revoked_at: Mapped[datetime | None]
    case: Mapped[Case] = relationship(back_populates="parties")


class TenantResponse(Base):
    __tablename__ = "tenant_responses"
    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), unique=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    source: Mapped[str] = mapped_column(String(10), default="token")  # token / fallback
    verified: Mapped[bool] = mapped_column(default=True)
    language: Mapped[str] = mapped_column(String(5), default="en")
    believed_owed: Mapped[float | None]
    pay_30d: Mapped[float | None]
    pay_monthly: Mapped[float | None]
    assistance_status: Mapped[str | None] = mapped_column(
        String(10)
    )  # none/applied/pending/approved
    assistance_amount: Mapped[float | None]
    wants: Mapped[str | None] = mapped_column(String(10))  # stay / move
    move_time_days: Mapped[int | None]
    repair_issues: Mapped[bool] = mapped_column(default=False)
    repair_details: Mapped[str | None] = mapped_column(Text)
    retaliation_concern: Mapped[bool] = mapped_column(default=False)
    notice_dispute: Mapped[bool] = mapped_column(default=False)
    payment_dispute: Mapped[bool] = mapped_column(default=False)
    legal_aid: Mapped[bool | None]
    willing_mediator: Mapped[str | None] = mapped_column(String(10))  # yes / no / unsure
    consent_text: Mapped[bool] = mapped_column(default=False)
    consent_call: Mapped[bool] = mapped_column(default=False)
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    preferred_language: Mapped[str | None] = mapped_column(String(20))
    started_at: Mapped[datetime | None]
    submitted_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None]
    revision: Mapped[int] = mapped_column(default=1)
    party: Mapped[Party] = relationship()


class LandlordResponse(Base):
    __tablename__ = "landlord_responses"
    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), unique=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    source: Mapped[str] = mapped_column(String(10), default="token")  # token / policy / fallback
    verified: Mapped[bool] = mapped_column(default=True)
    language: Mapped[str] = mapped_column(String(5), default="en")
    amount_owed_now: Mapped[float | None]
    fees_included: Mapped[float | None]
    accepts_plan: Mapped[bool | None]
    max_plan_months: Mapped[int | None]
    min_to_dismiss: Mapped[float | None]
    min_to_dismiss_pct: Mapped[float | None]  # from a standing policy
    waive_late_fees: Mapped[bool | None]
    waive_attorney_fees: Mapped[bool | None]
    extend_moveout: Mapped[bool | None]
    extend_moveout_days: Mapped[int | None]
    goal: Mapped[str | None] = mapped_column(String(20))
    willing_mediator: Mapped[str | None] = mapped_column(String(10))
    started_at: Mapped[datetime | None]
    submitted_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None]
    revision: Mapped[int] = mapped_column(default=1)
    party: Mapped[Party] = relationship()


class IntakeTiming(Base):
    """One row per intake form open, for the usability report."""

    __tablename__ = "intake_timings"
    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"), index=True)
    plaintiff_id: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(10), default="case")  # case / policy / fallback
    language: Mapped[str] = mapped_column(String(5), default="en")
    opened_at: Mapped[datetime]
    submitted_at: Mapped[datetime | None]
    is_synthetic: Mapped[bool] = mapped_column(default=False)


class FallbackSubmission(Base):
    """Case number + last name lookups. Never reveals case details to the submitter."""

    __tablename__ = "fallback_submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    role_claimed: Mapped[str] = mapped_column(String(10))
    case_number_entered: Mapped[str] = mapped_column(String(80))
    last_name_entered: Mapped[str] = mapped_column(String(120))
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    matched_party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"))
    # matched_unverified / review / attached / rejected
    status: Mapped[str] = mapped_column(String(20), default="review")
    created_at: Mapped[datetime]
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None]
    note: Mapped[str | None] = mapped_column(Text)


class CaseScore(Base):
    __tablename__ = "case_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    model_version: Mapped[str] = mapped_column(String(40))
    computed_at: Mapped[datetime]
    base_prob: Mapped[float]
    final_prob: Mapped[float]
    potential: Mapped[str] = mapped_column(String(40))
    base_tier: Mapped[str] = mapped_column(String(10))
    pathway: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[str] = mapped_column(String(10))
    legal_aid_flag: Mapped[bool] = mapped_column(default=False)
    overlap_class: Mapped[str | None] = mapped_column(String(20))
    reasons: Mapped[list] = mapped_column(JSON)
    features: Mapped[dict] = mapped_column(JSON)
    adjustments: Mapped[dict] = mapped_column(JSON)
    is_current: Mapped[bool] = mapped_column(default=True, index=True)


class StatusHistory(Base):
    __tablename__ = "status_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30))
    subtype: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(10))  # feed / staff / system
    import_id: Mapped[int | None] = mapped_column(ForeignKey("import_runs.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime]
    note: Mapped[str | None] = mapped_column(Text)


class FeedChange(Base):
    __tablename__ = "feed_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("import_runs.id"), index=True)
    field: Mapped[str] = mapped_column(String(40))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)


class CallLog(Base):
    __tablename__ = "call_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime]
    channel: Mapped[str] = mapped_column(String(20))  # phone / text / mail / email / in_person
    outcome: Mapped[str] = mapped_column(String(30))
    notes: Mapped[str | None] = mapped_column(Text)
    next_action: Mapped[str | None] = mapped_column(String(200))
    next_action_due: Mapped[date | None]


class ReconciliationFlag(Base):
    __tablename__ = "reconciliation_flags"
    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int | None] = mapped_column(ForeignKey("import_runs.id"), index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    working_status_before: Mapped[str | None] = mapped_column(String(30))
    feed_event: Mapped[str | None] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None]


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    username: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str | None] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(40), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), index=True)
    restricted: Mapped[bool] = mapped_column(default=False)
    details: Mapped[dict | None] = mapped_column(JSON)


class RetentionAggregate(Base):
    """Counts kept after intake details are purged."""

    __tablename__ = "retention_aggregates"
    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM of closure
    court_id: Mapped[str] = mapped_column(String(20))
    potential: Mapped[str | None] = mapped_column(String(40))
    pathway: Mapped[str | None] = mapped_column(String(40))
    outcome_status: Mapped[str] = mapped_column(String(30))
    n_cases: Mapped[int] = mapped_column(default=0)
    n_tenant_responses: Mapped[int] = mapped_column(default=0)
    n_landlord_responses: Mapped[int] = mapped_column(default=0)
    intake_seconds_total: Mapped[float] = mapped_column(default=0.0)
    intake_count: Mapped[int] = mapped_column(default=0)
    purged_at: Mapped[datetime]
