"""account audit and shared login throttle

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

ACCOUNT_AUDIT_GUARD_SQL = """
CREATE OR REPLACE FUNCTION forbid_account_audit_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'account_audit_event is append-only: % rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_account_audit_append_only ON account_audit_event;
CREATE TRIGGER trg_account_audit_append_only
BEFORE UPDATE OR DELETE ON account_audit_event
FOR EACH ROW EXECUTE FUNCTION forbid_account_audit_mutation();
"""

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "login_throttle",
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("username"),
    )
    op.create_table(
        "account_audit_event",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_reviewer_id", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("actor_role", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("subject_reviewer_id", sa.String(), nullable=False),
        sa.Column("previous_status", sa.String(), nullable=True),
        sa.Column("new_status", sa.String(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["actor_reviewer_id"], ["reviewer.id"]),
        sa.ForeignKeyConstraint(["subject_reviewer_id"], ["reviewer.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("seq"),
    )
    op.execute(ACCOUNT_AUDIT_GUARD_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_account_audit_append_only ON account_audit_event")
    op.execute("DROP FUNCTION IF EXISTS forbid_account_audit_mutation()")
    op.drop_table("account_audit_event")
    op.drop_table("login_throttle")
