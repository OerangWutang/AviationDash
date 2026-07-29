"""case audit actor identity and optimistic version counters

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_event",
        sa.Column("actor_reviewer_id", sa.String(), nullable=True),
    )
    op.create_foreign_key(
        "fk_audit_event_actor_reviewer",
        "audit_event",
        "reviewer",
        ["actor_reviewer_id"],
        ["id"],
    )
    # audit_event is append-only (trigger installed in 0001) — the trigger
    # rejects this backfill's UPDATE on any database that already has audit
    # rows, which is every real deployment. Suspend it for the backfill only.
    op.execute("ALTER TABLE audit_event DISABLE TRIGGER trg_audit_append_only")
    op.execute(
        """
        UPDATE audit_event a
        SET actor_reviewer_id = r.id
        FROM reviewer r
        WHERE a.actor_reviewer_id IS NULL
          AND a.actor = r.name
        """
    )
    op.execute("ALTER TABLE audit_event ENABLE TRIGGER trg_audit_append_only")
    op.add_column(
        "conflict",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "report_section",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_open_conflict_claim_pair
        ON conflict (
          case_id,
          LEAST(claim_a_id, claim_b_id),
          GREATEST(claim_a_id, claim_b_id)
        )
        WHERE status IN ('unresolved', 'escalated')
        """
    )
    op.alter_column("conflict", "version", server_default=None)
    op.alter_column("report_section", "version", server_default=None)


def downgrade() -> None:
    op.drop_column("report_section", "version")
    op.drop_index("uq_open_conflict_claim_pair", table_name="conflict")
    op.drop_column("conflict", "version")
    op.drop_constraint("fk_audit_event_actor_reviewer", "audit_event", type_="foreignkey")
    op.drop_column("audit_event", "actor_reviewer_id")
