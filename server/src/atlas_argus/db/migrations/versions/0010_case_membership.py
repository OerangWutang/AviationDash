"""case membership boundary for matter access

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ROLE_VALUES = (
    "'Accident Reconstruction Expert'",
    "'Claims Reviewer'",
    "'Safety Investigator'",
    "'Senior Aviation Counsel'",
)


def upgrade() -> None:
    op.create_table(
        "case_member",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("reviewer_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.CheckConstraint(f"role IN ({', '.join(ROLE_VALUES)})", name="ck_case_member_role"),
        sa.ForeignKeyConstraint(["case_id"], ["case_file.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["reviewer.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "reviewer_id", name="uq_case_member_case_reviewer"),
    )
    op.execute(
        """
        INSERT INTO case_member (id, case_id, reviewer_id, role, is_active)
        SELECT 'cm-' || c.id || '-' || r.id, c.id, r.id, r.role, true
          FROM case_file c
         CROSS JOIN reviewer r
        ON CONFLICT (case_id, reviewer_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("case_member")
