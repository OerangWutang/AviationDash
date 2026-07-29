"""document ingestion: stored files, versioned extraction, verified quotes

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-29

Until now a source document was metadata plus an unverifiable hash, and a
claim's ``quote``/``page_ref`` were free text nothing could check. This adds
the evidence the doctrine already assumed existed:

- ``source_document_file`` holds the uploaded bytes and the authoritative
  content hash, split off ``source_document`` so ordinary case reads never
  carry multi-megabyte blobs.
- ``source_extraction_run`` / ``source_page_extraction`` hold *versioned*
  per-page text. Extraction is append-only and re-runnable rather than
  overwritten, so improving OCR later cannot silently rewrite the text a
  historical claim was verified against.
- ``claim`` gains the immutable page it was checked against, a verification
  state, and the hash of exactly what was checked, so an edit to the quote,
  source, or page cannot leave a stale "verified" label behind.
- ``claim_quote_verification`` records human attestation and invalidation as
  structured, queryable rows rather than prose in an audit detail string.

Three database guards carry doctrine the service layer cannot enforce alone:
the four new tables are append-only; ``active_extraction_run_id`` is set once
(re-pointing a source at a different run is a reviewed workflow that does not
exist yet, so the database refuses it); and the claim verification columns
reject any write that did not come through the verification helper.

RLS on the three ingestion tables is privilege-aware, not merely
membership-aware: these rows hold real document bytes and page text, so
``atlas_argus_source_visible`` mirrors ``_source_visible_to`` in services.py.
The pre-existing ``source_document``/``claim`` policies are deliberately left
alone — tightening those is a separate, already-shipped-behavior change.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from atlas_argus.db.guards import (
    ACTIVE_EXTRACTION_RUN_GUARD_SQL,
    CLAIM_QUOTE_VERIFICATION_GUARD_SQL,
    CLAIM_VERIFICATION_FIELD_GUARD_SQL,
    SOURCE_DOCUMENT_FILE_GUARD_SQL,
    SOURCE_EXTRACTION_RUN_GUARD_SQL,
    SOURCE_PAGE_EXTRACTION_GUARD_SQL,
    SOURCE_PRIVILEGE_RLS_SQL,
    SOURCE_PRIVILEGE_SCOPED_TABLES,
)

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EXTRACTION_METHODS = ("failed", "native", "ocr")
EXTRACTION_FAILURE_CODES = (
    "invalid_media_box",
    "native_extract_error",
    "ocr_timeout",
    "ocr_unavailable",
    "page_parse_error",
    "pixel_limit",
    "raster_timeout",
    "total_text_budget_exhausted",
)
QUOTE_VERIFICATIONS = (
    "legacy_unverified",
    "manual_verification_required",
    "manually_verified",
    "verified_exact",
)
QUOTE_VERIFICATION_EVENTS = ("manual_verified", "verification_invalidated")
REVIEWER_ROLES = (
    "Accident Reconstruction Expert",
    "Claims Reviewer",
    "Safety Investigator",
    "Senior Aviation Counsel",
)


def _in(column: str, values: Sequence[str]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def _policy_name(table: str) -> str:
    return f"rls_{table}_source_visible"


def upgrade() -> None:
    op.create_table(
        "source_document_file",
        sa.Column("source_document_id", sa.String(), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("content_sha256", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("idempotency_fingerprint", sa.String(), nullable=True),
        sa.CheckConstraint("size_bytes >= 1", name="ck_source_file_size_bytes"),
        sa.CheckConstraint("page_count >= 1", name="ck_source_file_page_count"),
        sa.ForeignKeyConstraint(
            ["source_document_id", "case_id"],
            ["source_document.id", "source_document.case_id"],
            name="fk_source_file_source_same_case",
        ),
        sa.PrimaryKeyConstraint("source_document_id"),
        # Postgres permits many NULLs under a plain unique constraint, so an
        # upload without an idempotency key needs no special case.
        sa.UniqueConstraint("case_id", "idempotency_key", name="uq_source_file_idempotency"),
    )
    # Duplicate *detection*, deliberately not a uniqueness rule: identical
    # bytes can legitimately enter a matter twice through different custodians
    # or productions, and those are distinct evidentiary records.
    op.create_index(
        "ix_source_file_case_content_hash",
        "source_document_file",
        ["case_id", "content_sha256"],
    )

    op.create_table(
        "source_extraction_run",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_document_id", sa.String(), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parser_version", sa.String(), nullable=False),
        sa.Column("ocr_engine_version", sa.String(), nullable=False),
        sa.Column("ocr_languages", sa.String(), nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("ocr_page_count", sa.Integer(), nullable=False),
        sa.Column("failed_page_count", sa.Integer(), nullable=False),
        sa.Column("manifest_sha256", sa.String(), nullable=False),
        sa.CheckConstraint("page_count >= 0", name="ck_extraction_run_page_count"),
        sa.ForeignKeyConstraint(
            ["source_document_id", "case_id"],
            ["source_document.id", "source_document.case_id"],
            name="fk_extraction_run_source_same_case",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "source_document_id", "case_id", name="uq_extraction_run_id_source_case"
        ),
    )

    op.create_table(
        "source_page_extraction",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("extraction_run_id", sa.String(), nullable=False),
        sa.Column("source_document_id", sa.String(), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("extraction_method", sa.String(), nullable=False),
        sa.Column("ocr_confidence_bps", sa.Integer(), nullable=True),
        sa.Column(
            "text_truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.Column("failure_detail", sa.String(), nullable=True),
        sa.Column("text_sha256", sa.String(), nullable=False),
        sa.CheckConstraint("page_number >= 1", name="ck_source_page_number"),
        sa.CheckConstraint(
            _in("extraction_method", EXTRACTION_METHODS), name="ck_source_page_method"
        ),
        sa.CheckConstraint(
            "ocr_confidence_bps IS NULL "
            "OR (ocr_confidence_bps >= 0 AND ocr_confidence_bps <= 10000)",
            name="ck_source_page_confidence",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR " + _in("failure_code", EXTRACTION_FAILURE_CODES),
            name="ck_source_page_failure_code",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id", "case_id"],
            ["source_document.id", "source_document.case_id"],
            name="fk_source_page_source_same_case",
        ),
        # Proves the run belongs to this exact source and case — a bare
        # extraction_run_id FK would only prove the run exists somewhere.
        sa.ForeignKeyConstraint(
            ["extraction_run_id", "source_document_id", "case_id"],
            [
                "source_extraction_run.id",
                "source_extraction_run.source_document_id",
                "source_extraction_run.case_id",
            ],
            name="fk_source_page_run_same_source",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("extraction_run_id", "page_number", name="uq_source_page_run_number"),
        sa.UniqueConstraint(
            "id", "source_document_id", "case_id", name="uq_source_page_id_source_case"
        ),
    )

    op.add_column(
        "source_document", sa.Column("active_extraction_run_id", sa.String(), nullable=True)
    )
    # Circular reference (source -> run -> source), so the constraint is added
    # after both tables exist rather than inline.
    op.create_foreign_key(
        "fk_source_active_run_same_source",
        "source_document",
        "source_extraction_run",
        ["active_extraction_run_id", "id", "case_id"],
        ["id", "source_document_id", "case_id"],
    )

    op.add_column("claim", sa.Column("source_page_extraction_id", sa.String(), nullable=True))
    op.add_column(
        "claim",
        sa.Column(
            "quote_verification",
            sa.String(),
            nullable=False,
            server_default="legacy_unverified",
        ),
    )
    op.add_column(
        "claim", sa.Column("quote_verification_basis_sha256", sa.String(), nullable=True)
    )
    op.create_check_constraint(
        "ck_claim_quote_verification", "claim", _in("quote_verification", QUOTE_VERIFICATIONS)
    )
    op.create_foreign_key(
        "fk_claim_page_same_source",
        "claim",
        "source_page_extraction",
        ["source_page_extraction_id", "source_document_id", "case_id"],
        ["id", "source_document_id", "case_id"],
    )

    op.create_table(
        "claim_quote_verification",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("claim_id", sa.String(), nullable=False),
        sa.Column("reviewer_id", sa.String(), nullable=False),
        sa.Column("matter_role", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("previous_verification", sa.String(), nullable=False),
        sa.Column("new_verification", sa.String(), nullable=False),
        sa.Column("verification_basis_sha256", sa.String(), nullable=True),
        sa.Column("source_page_extraction_id", sa.String(), nullable=True),
        sa.CheckConstraint(
            _in("event_type", QUOTE_VERIFICATION_EVENTS), name="ck_quote_verification_event"
        ),
        sa.CheckConstraint(
            _in("previous_verification", QUOTE_VERIFICATIONS), name="ck_quote_verification_prev"
        ),
        sa.CheckConstraint(
            _in("new_verification", QUOTE_VERIFICATIONS), name="ck_quote_verification_new"
        ),
        sa.CheckConstraint(_in("matter_role", REVIEWER_ROLES), name="ck_quote_verification_role"),
        sa.ForeignKeyConstraint(
            ["claim_id", "case_id"],
            ["claim.id", "claim.case_id"],
            name="fk_quote_verification_claim_same_case",
        ),
        sa.ForeignKeyConstraint(["reviewer_id"], ["reviewer.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("seq"),
    )

    op.execute(SOURCE_DOCUMENT_FILE_GUARD_SQL)
    op.execute(SOURCE_EXTRACTION_RUN_GUARD_SQL)
    op.execute(SOURCE_PAGE_EXTRACTION_GUARD_SQL)
    op.execute(CLAIM_QUOTE_VERIFICATION_GUARD_SQL)
    op.execute(ACTIVE_EXTRACTION_RUN_GUARD_SQL)
    op.execute(CLAIM_VERIFICATION_FIELD_GUARD_SQL)

    op.execute("CREATE SEQUENCE IF NOT EXISTS source_id_seq")
    op.execute("CREATE SEQUENCE IF NOT EXISTS extraction_run_id_seq")

    op.execute(SOURCE_PRIVILEGE_RLS_SQL)
    for table in SOURCE_PRIVILEGE_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {_policy_name(table)}
                ON {table}
                USING (atlas_argus_source_visible(source_document_id, case_id))
                WITH CHECK (atlas_argus_source_visible(source_document_id, case_id))
            """
        )
    # Verification history follows the claim's own case membership; the claim
    # row it describes is already privilege-filtered by the service layer.
    op.execute("ALTER TABLE claim_quote_verification ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY rls_claim_quote_verification_active_case_member
            ON claim_quote_verification
            USING (atlas_argus_active_case_member(case_id))
            WITH CHECK (atlas_argus_active_case_member(case_id))
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS rls_claim_quote_verification_active_case_member "
        "ON claim_quote_verification"
    )
    op.execute("ALTER TABLE claim_quote_verification DISABLE ROW LEVEL SECURITY")
    for table in reversed(SOURCE_PRIVILEGE_SCOPED_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {_policy_name(table)} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS atlas_argus_source_visible(text, text)")

    op.execute("DROP SEQUENCE IF EXISTS extraction_run_id_seq")
    op.execute("DROP SEQUENCE IF EXISTS source_id_seq")

    op.execute("DROP TRIGGER IF EXISTS trg_claim_verification_fields_managed ON claim")
    op.execute("DROP FUNCTION IF EXISTS forbid_unmanaged_claim_verification_write()")
    op.execute("DROP TRIGGER IF EXISTS trg_source_document_active_run_set_once ON source_document")
    op.execute("DROP FUNCTION IF EXISTS forbid_active_extraction_run_change()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_claim_quote_verification_append_only "
        "ON claim_quote_verification"
    )
    op.execute("DROP FUNCTION IF EXISTS forbid_claim_quote_verification_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_source_page_extraction_append_only ON source_page_extraction"
    )
    op.execute("DROP FUNCTION IF EXISTS forbid_source_page_extraction_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_source_extraction_run_append_only ON source_extraction_run"
    )
    op.execute("DROP FUNCTION IF EXISTS forbid_source_extraction_run_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_source_document_file_append_only ON source_document_file"
    )
    op.execute("DROP FUNCTION IF EXISTS forbid_source_document_file_mutation()")

    op.drop_table("claim_quote_verification")
    op.drop_constraint("fk_claim_page_same_source", "claim", type_="foreignkey")
    op.drop_constraint("ck_claim_quote_verification", "claim", type_="check")
    op.drop_column("claim", "quote_verification_basis_sha256")
    op.drop_column("claim", "quote_verification")
    op.drop_column("claim", "source_page_extraction_id")
    op.drop_constraint("fk_source_active_run_same_source", "source_document", type_="foreignkey")
    op.drop_column("source_document", "active_extraction_run_id")
    op.drop_table("source_page_extraction")
    op.drop_table("source_extraction_run")
    op.drop_index("ix_source_file_case_content_hash", table_name="source_document_file")
    op.drop_table("source_document_file")
