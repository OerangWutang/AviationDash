"""allow reviewer account events in the append-only audit trail

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-11
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_SUBJECTS = "'claim', 'conflict', 'export', 'report_section', 'source'"
NEW_SUBJECTS = "'claim', 'conflict', 'export', 'report_section', 'reviewer', 'source'"


def upgrade() -> None:
    op.drop_constraint("ck_audit_subject", "audit_event", type_="check")
    op.create_check_constraint(
        "ck_audit_subject",
        "audit_event",
        f"subject_type IN ({NEW_SUBJECTS})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_subject", "audit_event", type_="check")
    # NOT VALID: re-adding the narrower constraint must not crash a downgrade
    # once real 'reviewer' rows exist (this migration's whole purpose is to
    # allow them). NOT VALID skips validating existing rows while still
    # enforcing the check on every future insert/update.
    op.execute(
        f"ALTER TABLE audit_event ADD CONSTRAINT ck_audit_subject "
        f"CHECK (subject_type IN ({OLD_SUBJECTS})) NOT VALID"
    )
