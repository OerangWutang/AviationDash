"""store role-independent current approval projection

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-05

Existing approvals are conservatively invalidated during upgrade. Counsel must
reapprove them once; guessing that an old evidence digest is current would be
unsafe, and recomputing the Python canonical digest inside a migration would
make the historical revision depend on mutable runtime code.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "report_section",
        sa.Column(
            "approval_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("report_section", "approval_current")
