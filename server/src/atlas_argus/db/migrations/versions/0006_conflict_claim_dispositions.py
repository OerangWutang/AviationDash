"""per-conflict claim dispositions

A claim's global status is now the aggregate of what each of its conflicts says
about it, rather than being overwritten by whichever conflict was decided last.
Each conflict records its disposition for both of its claims; existing rows are
backfilled from the conflict status (and, for source-unreliable conflicts, the
discredited claim in the latest decision).

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-11
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DISPOSITION_CHECK = (
    "{col} IN ('disputed', 'escalated', 'preserved', 'rejected', "
    "'superseded', 'supported', 'unreviewed')"
)


def upgrade() -> None:
    # Temporary server_default so ADD COLUMN succeeds on existing rows; dropped
    # below once the backfill has written the real per-claim dispositions.
    op.execute(
        "ALTER TABLE conflict "
        "ADD COLUMN claim_a_disposition VARCHAR NOT NULL DEFAULT 'disputed', "
        "ADD COLUMN claim_b_disposition VARCHAR NOT NULL DEFAULT 'disputed'"
    )
    # Symmetric statuses derive purely from the conflict status.
    op.execute(
        """
        UPDATE conflict SET
          claim_a_disposition = CASE status
            WHEN 'accepted_claim_a' THEN 'supported'
            WHEN 'accepted_claim_b' THEN 'superseded'
            WHEN 'preserved_both' THEN 'preserved'
            WHEN 'escalated' THEN 'escalated'
            WHEN 'closed' THEN 'unreviewed'
            ELSE 'disputed'
          END,
          claim_b_disposition = CASE status
            WHEN 'accepted_claim_a' THEN 'superseded'
            WHEN 'accepted_claim_b' THEN 'supported'
            WHEN 'preserved_both' THEN 'preserved'
            WHEN 'escalated' THEN 'escalated'
            WHEN 'closed' THEN 'unreviewed'
            ELSE 'disputed'
          END
        WHERE status <> 'source_unreliable'
        """
    )
    # source_unreliable is asymmetric: the discredited claim (the latest
    # decision's selected claim) is rejected; the survivor returns to review.
    op.execute(
        """
        UPDATE conflict c SET
          claim_a_disposition =
            CASE WHEN d.selected_claim_id = c.claim_a_id THEN 'rejected'
                 ELSE 'unreviewed' END,
          claim_b_disposition =
            CASE WHEN d.selected_claim_id = c.claim_b_id THEN 'rejected'
                 ELSE 'unreviewed' END
        FROM (
          SELECT DISTINCT ON (conflict_id) conflict_id, selected_claim_id
          FROM review_decision
          ORDER BY conflict_id, created_at DESC, id DESC
        ) d
        WHERE c.id = d.conflict_id AND c.status = 'source_unreliable'
        """
    )
    op.create_check_constraint(
        "ck_conflict_disp_a", "conflict", _DISPOSITION_CHECK.format(col="claim_a_disposition")
    )
    op.create_check_constraint(
        "ck_conflict_disp_b", "conflict", _DISPOSITION_CHECK.format(col="claim_b_disposition")
    )
    # Drop the bootstrap default — the application always sets dispositions.
    op.execute(
        "ALTER TABLE conflict "
        "ALTER COLUMN claim_a_disposition DROP DEFAULT, "
        "ALTER COLUMN claim_b_disposition DROP DEFAULT"
    )


def downgrade() -> None:
    op.drop_constraint("ck_conflict_disp_b", "conflict", type_="check")
    op.drop_constraint("ck_conflict_disp_a", "conflict", type_="check")
    op.drop_column("conflict", "claim_b_disposition")
    op.drop_column("conflict", "claim_a_disposition")
