"""append-only integrity chain for review decisions

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-18

Review decisions are substantive adjudicative records, not mutable current
state. Give them a deterministic insert order, chain them independently per
case (through their conflicts), and install the same database append-only
guard used by the other evidentiary records.
"""

from types import SimpleNamespace
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from atlas_argus.db.guards import REVIEW_DECISION_GUARD_SQL
from atlas_argus.integrity import chain_sha256, review_decision_content

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill_review_decisions(bind) -> None:
    previous_hash_by_case: dict[str, str | None] = {}
    rows = bind.execute(
        sa.text(
            """
            SELECT d.id, d.seq, d.conflict_id, d.decision_type,
                   d.selected_claim_id, d.reasoning, d.reviewer_name,
                   d.reviewer_role, d.created_at, d.previous_status,
                   d.new_status, d.report_impact, c.case_id
              FROM review_decision d
              JOIN conflict c ON c.id = d.conflict_id
             ORDER BY c.case_id, d.seq
            """
        )
    ).mappings()
    for row in rows:
        case_id = row["case_id"]
        previous_hash = previous_hash_by_case.get(case_id)
        decision = SimpleNamespace(**dict(row))
        integrity_hash = chain_sha256(
            previous_hash=previous_hash,
            content=review_decision_content(decision),
        )
        bind.execute(
            sa.text(
                """
                UPDATE review_decision
                   SET previous_integrity_hash = :previous_hash,
                       integrity_hash = :integrity_hash
                 WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "previous_hash": previous_hash,
                "integrity_hash": integrity_hash,
            },
        )
        previous_hash_by_case[case_id] = integrity_hash


def upgrade() -> None:
    op.add_column(
        "review_decision",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    )
    op.create_unique_constraint(
        "uq_review_decision_seq", "review_decision", ["seq"]
    )
    op.create_index(
        "ix_review_decision_conflict_seq",
        "review_decision",
        ["conflict_id", "seq"],
    )
    op.add_column(
        "review_decision",
        sa.Column("previous_integrity_hash", sa.String(), nullable=True),
    )
    op.add_column(
        "review_decision", sa.Column("integrity_hash", sa.String(), nullable=True)
    )
    _backfill_review_decisions(op.get_bind())
    op.alter_column("review_decision", "integrity_hash", nullable=False)
    op.execute(REVIEW_DECISION_GUARD_SQL)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_review_decision_append_only ON review_decision"
    )
    op.execute("DROP FUNCTION IF EXISTS forbid_review_decision_mutation()")
    op.drop_column("review_decision", "integrity_hash")
    op.drop_column("review_decision", "previous_integrity_hash")
    op.drop_index("ix_review_decision_conflict_seq", table_name="review_decision")
    op.drop_constraint("uq_review_decision_seq", "review_decision", type_="unique")
    op.drop_column("review_decision", "seq")
