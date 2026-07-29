"""report revisions and packet artifacts

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from atlas_argus.db.guards import PACKET_ARTIFACT_GUARD_SQL, REPORT_REVISION_GUARD_SQL

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "report_section",
        sa.Column("active_revision_id", sa.String(), nullable=True),
    )
    op.create_table(
        "report_section_revision",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("section_id", sa.String(), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("paragraph_ref", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("claim_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("author_reviewer_id", sa.String(), nullable=False),
        sa.Column("author", sa.String(), nullable=False),
        sa.Column("author_role", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision_reason", sa.Text(), nullable=False),
        sa.Column("parent_revision_id", sa.String(), nullable=True),
        sa.Column("approval_state", sa.String(), nullable=False, server_default="draft"),
        sa.ForeignKeyConstraint(["author_reviewer_id"], ["reviewer.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["case_file.id"]),
        sa.ForeignKeyConstraint(["parent_revision_id"], ["report_section_revision.id"]),
        sa.ForeignKeyConstraint(["section_id"], ["report_section.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_report_revision_section_created",
        "report_section_revision",
        ["section_id", "created_at"],
    )
    op.create_table(
        "packet_artifact",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("packet_type", sa.String(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_by_reviewer_id", sa.String(), nullable=False),
        sa.Column("generated_by_name", sa.String(), nullable=False),
        sa.Column("generated_by_role", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("body_sha256", sa.String(), nullable=False),
        sa.Column("document", sa.Text(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["case_file.id"]),
        sa.ForeignKeyConstraint(["generated_by_reviewer_id"], ["reviewer.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_packet_artifact_case_generated",
        "packet_artifact",
        ["case_id", "generated_at"],
    )
    op.execute(REPORT_REVISION_GUARD_SQL)
    op.execute(PACKET_ARTIFACT_GUARD_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_packet_artifact_append_only ON packet_artifact")
    op.execute("DROP FUNCTION IF EXISTS forbid_packet_artifact_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_report_revision_append_only ON report_section_revision"
    )
    op.execute("DROP FUNCTION IF EXISTS forbid_report_revision_mutation()")
    op.drop_index("ix_packet_artifact_case_generated", table_name="packet_artifact")
    op.drop_table("packet_artifact")
    op.drop_index("ix_report_revision_section_created", table_name="report_section_revision")
    op.drop_table("report_section_revision")
    op.drop_column("report_section", "active_revision_id")
