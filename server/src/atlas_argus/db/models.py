"""SQLAlchemy models.

Mirrors the frontend domain model with database-level doctrine the client
cannot provide:
- CHECK constraints on every status enum, confidence range, and the
  two-distinct-claims rule for conflicts.
- ``audit_event`` is append-only (trigger installed by ``guards.py``).
- ``relatedConflictIds`` and ``decisionIds`` are DERIVED from the conflict and
  decision tables at read time, never stored — no denormalized copies to
  drift.
- Timestamps are TIMESTAMPTZ; the API serializes them as ISO-8601 UTC ("Z").
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, deferred, mapped_column

from ..domain import types as t


def _in(column: str, values: frozenset[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in sorted(values))
    return f"{column} IN ({quoted})"


class Base(DeclarativeBase):
    pass


class CaseFile(Base):
    __tablename__ = "case_file"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    aircraft: Mapped[str] = mapped_column(String)
    accident_date: Mapped[str] = mapped_column(String)
    location: Mapped[str] = mapped_column(String)
    matter_type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    docket_ref: Mapped[str] = mapped_column(String)


class Reviewer(Base):
    __tablename__ = "reviewer"
    __table_args__ = (CheckConstraint(_in("role", t.REVIEWER_ROLES), name="ck_reviewer_role"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    username: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(String)
    #: Forced rotation on next sign-in (production seeds set this).
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    mfa_secret: Mapped[str | None] = mapped_column(String, nullable=True)
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Deactivated reviewers can neither sign in nor use existing sessions.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class CaseMember(Base):
    __tablename__ = "case_member"
    __table_args__ = (
        CheckConstraint(_in("role", t.REVIEWER_ROLES), name="ck_case_member_role"),
        UniqueConstraint("case_id", "reviewer_id", name="uq_case_member_case_reviewer"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_file.id"))
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("reviewer.id"))
    role: Mapped[str] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class AuthSession(Base):
    """Server-side, revocable sessions. Only the SHA-256 of the bearer token
    is stored — a database leak does not leak live sessions."""

    __tablename__ = "auth_session"

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("reviewer.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mfa_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LoginThrottle(Base):
    """Shared login throttle state keyed by normalized username.

    This lives in Postgres so lockouts are consistent across API processes.
    """

    __tablename__ = "login_throttle"

    username: Mapped[str] = mapped_column(String, primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AccountAuditEvent(Base):
    """Append-only account governance audit.

    Kept outside the case evidence audit so sample-case resets cannot erase
    reviewer provisioning, deactivation, or password reset history.
    """

    __tablename__ = "account_audit_event"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_reviewer_id: Mapped[str] = mapped_column(ForeignKey("reviewer.id"))
    actor: Mapped[str] = mapped_column(String)
    actor_role: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    subject_reviewer_id: Mapped[str] = mapped_column(ForeignKey("reviewer.id"))
    previous_status: Mapped[str | None] = mapped_column(String, nullable=True)
    new_status: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[str] = mapped_column(Text)
    previous_integrity_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    integrity_hash: Mapped[str] = mapped_column(String)


class SourceDocument(Base):
    __tablename__ = "source_document"
    __table_args__ = (
        CheckConstraint(_in("type", t.SOURCE_TYPES), name="ck_source_type"),
        CheckConstraint(
            _in("privilege_status", t.PRIVILEGE_STATUSES), name="ck_source_privilege"
        ),
        # The active run must belong to *this* source in *this* case. Circular
        # with source_extraction_run, so use_alter — same shape as
        # report_section.active_revision_id below.
        ForeignKeyConstraint(
            ["active_extraction_run_id", "id", "case_id"],
            [
                "source_extraction_run.id",
                "source_extraction_run.source_document_id",
                "source_extraction_run.case_id",
            ],
            name="fk_source_active_run_same_source",
            use_alter=True,
        ),
        UniqueConstraint("id", "case_id", name="uq_source_document_id_case"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_file.id"))
    title: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)
    origin: Mapped[str] = mapped_column(String)
    custodian: Mapped[str] = mapped_column(String)
    docket_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: Legacy display projection only. For an uploaded source the authoritative
    #: hash is ``SourceDocumentFile.content_sha256``, computed from the real
    #: bytes; this column predates uploads and is unverifiable for seeded rows.
    sha256: Mapped[str] = mapped_column(String)
    privilege_status: Mapped[str] = mapped_column(String)
    #: list[{at, actor, action}] — value objects, kept verbatim.
    custody: Mapped[list] = mapped_column(JSONB)
    #: Set once by ingestion (guarded by a trigger — see ``guards.py``). NULL
    #: means "no extracted text", which is the legacy/seeded shape.
    active_extraction_run_id: Mapped[str | None] = mapped_column(String, nullable=True)


class SourceDocumentFile(Base):
    """The uploaded bytes, split off ``source_document`` so ordinary case reads
    never drag multi-megabyte blobs along. Append-only."""

    __tablename__ = "source_document_file"
    __table_args__ = (
        CheckConstraint("size_bytes >= 1", name="ck_source_file_size_bytes"),
        CheckConstraint("page_count >= 1", name="ck_source_file_page_count"),
        ForeignKeyConstraint(
            ["source_document_id", "case_id"],
            ["source_document.id", "source_document.case_id"],
            name="fk_source_file_source_same_case",
        ),
        UniqueConstraint("case_id", "idempotency_key", name="uq_source_file_idempotency"),
        Index("ix_source_file_case_content_hash", "case_id", "content_sha256"),
    )

    source_document_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(String)
    #: Deferred so a stray full-entity load cannot pull the whole PDF into
    #: memory. Every summary read (page count, hash, filename) must select
    #: scalar columns instead.
    content: Mapped[bytes] = deferred(mapped_column(LargeBinary))
    content_sha256: Mapped[str] = mapped_column(String)
    mime_type: Mapped[str] = mapped_column(String)
    original_filename: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    page_count: Mapped[int] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: Retry safety for a request that can legitimately take minutes.
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)


class SourceExtractionRun(Base):
    """One immutable pass of extraction over one source. Versioned rather than
    overwritten so improving OCR later cannot rewrite the text a historical
    claim was verified against. Append-only."""

    __tablename__ = "source_extraction_run"
    __table_args__ = (
        CheckConstraint("page_count >= 0", name="ck_extraction_run_page_count"),
        ForeignKeyConstraint(
            ["source_document_id", "case_id"],
            ["source_document.id", "source_document.case_id"],
            name="fk_extraction_run_source_same_case",
        ),
        # Lets pages and the active-run pointer reference the (run, source,
        # case) triple, so a run created for source A can never be attached to
        # source B — proving it in the schema, not only in Python.
        UniqueConstraint(
            "id", "source_document_id", "case_id", name="uq_extraction_run_id_source_case"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_document_id: Mapped[str] = mapped_column(String)
    case_id: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    parser_version: Mapped[str] = mapped_column(String)
    ocr_engine_version: Mapped[str] = mapped_column(String)
    ocr_languages: Mapped[str] = mapped_column(String)
    #: The bounded limits actually in force for this run, so a later
    #: verification can tell a truncation from a config change.
    config_snapshot: Mapped[dict] = mapped_column(JSONB)
    page_count: Mapped[int] = mapped_column(Integer)
    ocr_page_count: Mapped[int] = mapped_column(Integer)
    failed_page_count: Mapped[int] = mapped_column(Integer)
    manifest_sha256: Mapped[str] = mapped_column(String)


class SourcePageExtraction(Base):
    """One page's extracted text within one run. Append-only: a claim verified
    against this text must not be able to become false by a later edit."""

    __tablename__ = "source_page_extraction"
    __table_args__ = (
        CheckConstraint("page_number >= 1", name="ck_source_page_number"),
        CheckConstraint(
            _in("extraction_method", t.EXTRACTION_METHODS), name="ck_source_page_method"
        ),
        CheckConstraint(
            "ocr_confidence_bps IS NULL "
            "OR (ocr_confidence_bps >= 0 AND ocr_confidence_bps <= 10000)",
            name="ck_source_page_confidence",
        ),
        CheckConstraint(
            "failure_code IS NULL OR "
            + _in("failure_code", t.EXTRACTION_FAILURE_CODES),
            name="ck_source_page_failure_code",
        ),
        ForeignKeyConstraint(
            ["source_document_id", "case_id"],
            ["source_document.id", "source_document.case_id"],
            name="fk_source_page_source_same_case",
        ),
        ForeignKeyConstraint(
            ["extraction_run_id", "source_document_id", "case_id"],
            [
                "source_extraction_run.id",
                "source_extraction_run.source_document_id",
                "source_extraction_run.case_id",
            ],
            name="fk_source_page_run_same_source",
        ),
        UniqueConstraint("extraction_run_id", "page_number", name="uq_source_page_run_number"),
        UniqueConstraint(
            "id", "source_document_id", "case_id", name="uq_source_page_id_source_case"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    extraction_run_id: Mapped[str] = mapped_column(String)
    source_document_id: Mapped[str] = mapped_column(String)
    case_id: Mapped[str] = mapped_column(String)
    page_number: Mapped[int] = mapped_column(Integer)
    extracted_text: Mapped[str] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String)
    #: Basis points (0–10000), not a float: the manifest hash has to be stable
    #: across serializations, and floats are not.
    ocr_confidence_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: True when the page hit the per-page byte cap. A quote *absent* from
    #: truncated text is unknown, not disproved.
    text_truncated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    failure_code: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(String, nullable=True)
    text_sha256: Mapped[str] = mapped_column(String)


class ClaimQuoteVerification(Base):
    """Who attested to (or invalidated) a claim's quote, when, in what matter
    role, why, and against exactly which immutable page. Append-only."""

    __tablename__ = "claim_quote_verification"
    __table_args__ = (
        CheckConstraint(
            _in("event_type", t.QUOTE_VERIFICATION_EVENTS), name="ck_quote_verification_event"
        ),
        CheckConstraint(
            _in("previous_verification", t.QUOTE_VERIFICATIONS),
            name="ck_quote_verification_prev",
        ),
        CheckConstraint(
            _in("new_verification", t.QUOTE_VERIFICATIONS), name="ck_quote_verification_new"
        ),
        CheckConstraint(_in("matter_role", t.REVIEWER_ROLES), name="ck_quote_verification_role"),
        ForeignKeyConstraint(
            ["claim_id", "case_id"],
            ["claim.id", "claim.case_id"],
            name="fk_quote_verification_claim_same_case",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    case_id: Mapped[str] = mapped_column(String)
    claim_id: Mapped[str] = mapped_column(String)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("reviewer.id"))
    matter_role: Mapped[str] = mapped_column(String)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_type: Mapped[str] = mapped_column(String)
    reasoning: Mapped[str] = mapped_column(Text)
    previous_verification: Mapped[str] = mapped_column(String)
    new_verification: Mapped[str] = mapped_column(String)
    verification_basis_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    source_page_extraction_id: Mapped[str | None] = mapped_column(String, nullable=True)


class Claim(Base):
    __tablename__ = "claim"
    __table_args__ = (
        CheckConstraint(_in("status", t.CLAIM_STATUSES), name="ck_claim_status"),
        CheckConstraint(
            _in("privilege_status", t.PRIVILEGE_STATUSES), name="ck_claim_privilege"
        ),
        CheckConstraint(
            _in("report_eligibility", t.REPORT_ELIGIBILITIES), name="ck_claim_eligibility"
        ),
        CheckConstraint(
            _in("evidence_quality", t.EVIDENCE_QUALITIES), name="ck_claim_quality"
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_claim_confidence"
        ),
        CheckConstraint(
            _in("quote_verification", t.QUOTE_VERIFICATIONS), name="ck_claim_quote_verification"
        ),
        ForeignKeyConstraint(
            ["source_document_id", "case_id"],
            ["source_document.id", "source_document.case_id"],
            name="fk_claim_source_same_case",
        ),
        # The cited page must belong to the cited source in this case — a
        # claim cannot point at a page of some other document.
        ForeignKeyConstraint(
            ["source_page_extraction_id", "source_document_id", "case_id"],
            [
                "source_page_extraction.id",
                "source_page_extraction.source_document_id",
                "source_page_extraction.case_id",
            ],
            name="fk_claim_page_same_source",
        ),
        UniqueConstraint("id", "case_id", name="uq_claim_id_case"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_file.id"))
    text: Mapped[str] = mapped_column(Text)
    source_document_id: Mapped[str] = mapped_column(ForeignKey("source_document.id"))
    #: The human/legal citation locator ("NTSB-000184", "p. 42, lines 8-17").
    #: Deliberately NOT the PDF's internal page index: an exhibit can restart
    #: numbering, use Bates stamps, or use roman front matter. Reviewer-owned.
    page_ref: Mapped[str] = mapped_column(String)
    quote: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    evidence_quality: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    privilege_status: Mapped[str] = mapped_column(String)
    report_eligibility: Mapped[str] = mapped_column(String)
    reviewer: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: The immutable extracted page this quote was checked against, when the
    #: source has been ingested. NULL for legacy/seeded sources.
    source_page_extraction_id: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Server-controlled (trigger-guarded); never accepted from a request body.
    quote_verification: Mapped[str] = mapped_column(
        String, default="legacy_unverified", server_default="legacy_unverified"
    )
    #: Hash of exactly what was verified, so an edit to the quote, source, or
    #: page cannot leave a stale "verified" label behind.
    quote_verification_basis_sha256: Mapped[str | None] = mapped_column(String, nullable=True)


class Conflict(Base):
    __tablename__ = "conflict"
    __table_args__ = (
        CheckConstraint(_in("status", t.CONFLICT_STATUSES), name="ck_conflict_status"),
        CheckConstraint(_in("conflict_type", t.CONFLICT_TYPES), name="ck_conflict_type"),
        CheckConstraint(_in("severity", t.CONFLICT_SEVERITIES), name="ck_conflict_severity"),
        CheckConstraint("claim_a_id <> claim_b_id", name="ck_conflict_distinct_claims"),
        CheckConstraint(
            _in("claim_a_disposition", t.CLAIM_STATUSES), name="ck_conflict_disp_a"
        ),
        CheckConstraint(
            _in("claim_b_disposition", t.CLAIM_STATUSES), name="ck_conflict_disp_b"
        ),
        ForeignKeyConstraint(
            ["claim_a_id", "case_id"],
            ["claim.id", "claim.case_id"],
            name="fk_conflict_claim_a_same_case",
        ),
        ForeignKeyConstraint(
            ["claim_b_id", "case_id"],
            ["claim.id", "claim.case_id"],
            name="fk_conflict_claim_b_same_case",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_file.id"))
    claim_a_id: Mapped[str] = mapped_column(ForeignKey("claim.id"))
    claim_b_id: Mapped[str] = mapped_column(ForeignKey("claim.id"))
    conflict_type: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    #: What this conflict says about each of its two claims. A claim's global
    #: ``status`` is the aggregate of these across every conflict it is in —
    #: stored, not derived from decision history, so the frontend (which sees
    #: conflicts but not decisions) can aggregate identically.
    claim_a_disposition: Mapped[str] = mapped_column(String)
    claim_b_disposition: Mapped[str] = mapped_column(String)


class ReviewDecision(Base):
    __tablename__ = "review_decision"
    __table_args__ = (
        CheckConstraint(_in("decision_type", t.DECISION_TYPES), name="ck_decision_type"),
        CheckConstraint(
            _in("previous_status", t.CONFLICT_STATUSES), name="ck_decision_prev_status"
        ),
        CheckConstraint(_in("new_status", t.CONFLICT_STATUSES), name="ck_decision_new_status"),
        CheckConstraint(_in("reviewer_role", t.REVIEWER_ROLES), name="ck_decision_role"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    #: Monotonic insert order. Decisions are chained independently per case;
    #: this is the deterministic order within each case after joining through
    #: the decision's conflict.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    conflict_id: Mapped[str] = mapped_column(ForeignKey("conflict.id"))
    decision_type: Mapped[str] = mapped_column(String)
    selected_claim_id: Mapped[str | None] = mapped_column(
        ForeignKey("claim.id"), nullable=True
    )
    reasoning: Mapped[str] = mapped_column(Text)
    reviewer_name: Mapped[str] = mapped_column(String)
    reviewer_role: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    previous_status: Mapped[str] = mapped_column(String)
    new_status: Mapped[str] = mapped_column(String)
    report_impact: Mapped[str] = mapped_column(Text)
    previous_integrity_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    integrity_hash: Mapped[str] = mapped_column(String)


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        CheckConstraint(_in("subject_type", t.AUDIT_SUBJECT_TYPES), name="ck_audit_subject"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    #: Monotonic insert order — the API returns events newest-first by seq.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_file.id"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_reviewer_id: Mapped[str | None] = mapped_column(
        ForeignKey("reviewer.id"), nullable=True
    )
    actor: Mapped[str] = mapped_column(String)
    actor_role: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    subject_type: Mapped[str] = mapped_column(String)
    subject_id: Mapped[str] = mapped_column(String)
    previous_status: Mapped[str | None] = mapped_column(String, nullable=True)
    new_status: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[str] = mapped_column(Text)
    report_eligibility_changed: Mapped[bool] = mapped_column(Boolean)
    previous_integrity_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    integrity_hash: Mapped[str] = mapped_column(String)


class ReportSection(Base):
    __tablename__ = "report_section"
    __table_args__ = (
        ForeignKeyConstraint(
            ["active_revision_id", "id"],
            ["report_section_revision.id", "report_section_revision.section_id"],
            name="fk_report_section_active_revision_same_section",
            use_alter=True,
        ),
        UniqueConstraint("id", "case_id", name="uq_report_section_id_case"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_file.id"))
    title: Mapped[str] = mapped_column(String)
    paragraph_ref: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    #: Ordered citation list — order is authorial, so a JSONB array, not a join table.
    claim_ids: Mapped[list] = mapped_column(JSONB)
    position: Mapped[int] = mapped_column(Integer)
    active_revision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    #: Role-independent projection of whether the active approved revision is
    #: still current. Evidence mutations invalidate it transactionally before
    #: privilege-filtered readers observe state; counsel-only packet generation
    #: additionally revalidates the complete evidence digest.
    approval_current: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )


class ReportSectionRevision(Base):
    """Immutable report text/citation history for reconstructable drafting."""

    __tablename__ = "report_section_revision"
    __table_args__ = (
        CheckConstraint(
            "approval_state <> 'approved' OR content_sha256 IS NOT NULL",
            name="ck_report_revision_approved_hash",
        ),
        ForeignKeyConstraint(
            ["section_id", "case_id"],
            ["report_section.id", "report_section.case_id"],
            name="fk_report_revision_section_same_case",
        ),
        UniqueConstraint("id", "section_id", name="uq_report_revision_id_section"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    section_id: Mapped[str] = mapped_column(ForeignKey("report_section.id"))
    case_id: Mapped[str] = mapped_column(ForeignKey("case_file.id"))
    title: Mapped[str] = mapped_column(String)
    paragraph_ref: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    claim_ids: Mapped[list] = mapped_column(JSONB)
    author_reviewer_id: Mapped[str] = mapped_column(ForeignKey("reviewer.id"))
    author: Mapped[str] = mapped_column(String)
    author_role: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision_reason: Mapped[str] = mapped_column(Text)
    parent_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("report_section_revision.id"), nullable=True
    )
    approval_state: Mapped[str] = mapped_column(String, default="draft", server_default="draft")
    #: Present only on production approvals. Binds the approval to the current
    #: cited claims, sources, quote verification, and conflict state.
    approval_evidence_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Chained off parent_revision_id's own integrity_hash — content_sha256
    #: alone proves nothing (an attacker who edits a row can always recompute
    #: a self-consistent hash for its own new content). Chaining means
    #: tampering with an older revision also invalidates every descendant's
    #: integrity_hash, unless the attacker rewrites the whole downstream
    #: lineage, not just the one row.
    previous_integrity_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    integrity_hash: Mapped[str] = mapped_column(String)


class PacketArtifact(Base):
    """Immutable generated packet body and manifest.

    The API still returns the document for immediate download, but this record
    preserves the exact artifact that was generated and audited.
    """

    __tablename__ = "packet_artifact"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "idempotency_key", name="uq_packet_artifact_idempotency"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    #: Monotonic insert order, assigned while generate_packet_op holds the
    #: per-case advisory lock — the chain's link and verification order.
    #: ``generated_at`` is a content field only; two concurrent generations
    #: can commit in an order that timestamps alone don't reliably reflect.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case_file.id"))
    packet_type: Mapped[str] = mapped_column(String)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generated_by_reviewer_id: Mapped[str] = mapped_column(ForeignKey("reviewer.id"))
    generated_by_name: Mapped[str] = mapped_column(String)
    generated_by_role: Mapped[str] = mapped_column(String)
    filename: Mapped[str] = mapped_column(String)
    body_sha256: Mapped[str] = mapped_column(String)
    document: Mapped[str] = deferred(mapped_column(Text))
    manifest: Mapped[dict] = mapped_column(JSONB)
    #: The paginated PDF exactly as produced. Deferred so listing artifacts
    #: never drags megabytes of document per row. NULL for artifacts generated
    #: before PDF export existed — deliberately not back-filled, since
    #: re-rendering one now would invent a document that was never served.
    pdf: Mapped[bytes | None] = deferred(mapped_column(LargeBinary, nullable=True))
    pdf_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)
    previous_integrity_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    integrity_hash: Mapped[str] = mapped_column(String)


# Paragraph references resolve to exactly one paragraph per case — enforced at
# the database, case-insensitively, as a backstop behind service validation.
Index(
    "ux_section_paragraph_ref",
    ReportSection.case_id,
    func.lower(ReportSection.paragraph_ref),
    unique=True,
)
