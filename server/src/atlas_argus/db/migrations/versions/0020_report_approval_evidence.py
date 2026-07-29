"""bind report approval to cited evidence state

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-29

Existing approved revisions remain historically intact, but receive NULL here
and therefore require explicit reapproval before future production. Inventing a
digest during migration would falsely claim counsel approved the evidence state
at migration time.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "report_section_revision",
        sa.Column("approval_evidence_sha256", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("report_section_revision", "approval_evidence_sha256")
