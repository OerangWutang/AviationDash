"""stabilize evidence metadata, packet retries, and query paths

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Frozen in this revision. Historical migrations must not import mutable
# runtime guard definitions.
SOURCE_DOCUMENT_METADATA_GUARD_SQL = """
CREATE OR REPLACE FUNCTION forbid_source_document_metadata_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'source_document custody metadata is immutable: DELETE rejected';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.case_id IS DISTINCT FROM OLD.case_id
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.type IS DISTINCT FROM OLD.type
       OR NEW.origin IS DISTINCT FROM OLD.origin
       OR NEW.custodian IS DISTINCT FROM OLD.custodian
       OR NEW.docket_ref IS DISTINCT FROM OLD.docket_ref
       OR NEW.ingested_at IS DISTINCT FROM OLD.ingested_at
       OR NEW.sha256 IS DISTINCT FROM OLD.sha256
       OR NEW.privilege_status IS DISTINCT FROM OLD.privilege_status
       OR NEW.custody IS DISTINCT FROM OLD.custody THEN
        RAISE EXCEPTION 'source_document custody metadata is immutable: UPDATE rejected';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_source_document_metadata_immutable ON source_document;
CREATE TRIGGER trg_source_document_metadata_immutable
    BEFORE UPDATE OR DELETE ON source_document
    FOR EACH ROW EXECUTE FUNCTION forbid_source_document_metadata_mutation();
"""


def upgrade() -> None:
    op.add_column("packet_artifact", sa.Column("idempotency_key", sa.String(), nullable=True))
    op.add_column(
        "packet_artifact", sa.Column("idempotency_fingerprint", sa.String(), nullable=True)
    )
    op.create_unique_constraint(
        "uq_packet_artifact_idempotency",
        "packet_artifact",
        ["case_id", "idempotency_key"],
    )

    op.create_index(
        "ix_case_member_reviewer_active",
        "case_member",
        ["reviewer_id", "is_active", "case_id"],
    )
    op.create_index(
        "ix_auth_session_reviewer_revoked",
        "auth_session",
        ["reviewer_id", "revoked_at"],
    )
    op.create_index("ix_auth_session_expires", "auth_session", ["expires_at"])
    op.create_index("ix_source_document_case", "source_document", ["case_id"])
    op.create_index("ix_claim_case", "claim", ["case_id"])
    op.create_index("ix_conflict_case", "conflict", ["case_id"])
    op.create_index("ix_audit_event_case_seq", "audit_event", ["case_id", "seq"])
    op.create_index(
        "ix_report_section_case_position", "report_section", ["case_id", "position"]
    )
    op.create_index(
        "ix_report_revision_case_section",
        "report_section_revision",
        ["case_id", "section_id"],
    )

    op.execute(SOURCE_DOCUMENT_METADATA_GUARD_SQL)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_source_document_metadata_immutable ON source_document"
    )
    op.execute("DROP FUNCTION IF EXISTS forbid_source_document_metadata_mutation()")

    op.drop_index("ix_report_revision_case_section", table_name="report_section_revision")
    op.drop_index("ix_report_section_case_position", table_name="report_section")
    op.drop_index("ix_audit_event_case_seq", table_name="audit_event")
    op.drop_index("ix_conflict_case", table_name="conflict")
    op.drop_index("ix_claim_case", table_name="claim")
    op.drop_index("ix_source_document_case", table_name="source_document")
    op.drop_index("ix_auth_session_expires", table_name="auth_session")
    op.drop_index("ix_auth_session_reviewer_revoked", table_name="auth_session")
    op.drop_index("ix_case_member_reviewer_active", table_name="case_member")

    op.drop_constraint(
        "uq_packet_artifact_idempotency", "packet_artifact", type_="unique"
    )
    op.drop_column("packet_artifact", "idempotency_fingerprint")
    op.drop_column("packet_artifact", "idempotency_key")
