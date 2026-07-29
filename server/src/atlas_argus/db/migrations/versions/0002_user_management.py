"""user management — password rotation and deactivation

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-11

- ``must_change_password``: forced rotation on first sign-in (set by the seed
  in production, where the initial password is shared out-of-band).
- ``password_changed_at``: account-level marker (the case audit trail stays
  case-pure; account events do not belong in it).
- ``is_active``: deactivated reviewers can neither sign in nor use existing
  sessions.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reviewer",
        sa.Column(
            "must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "reviewer",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "reviewer",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("reviewer", "is_active")
    op.drop_column("reviewer", "password_changed_at")
    op.drop_column("reviewer", "must_change_password")
