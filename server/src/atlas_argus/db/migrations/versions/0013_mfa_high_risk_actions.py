"""MFA state for high-risk legal and admin actions

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reviewer", sa.Column("mfa_secret", sa.String(), nullable=True))
    op.add_column(
        "reviewer",
        sa.Column("mfa_enabled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "auth_session",
        sa.Column("mfa_verified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("auth_session", "mfa_verified_at")
    op.drop_column("reviewer", "mfa_enabled_at")
    op.drop_column("reviewer", "mfa_secret")
