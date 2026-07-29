"""race-free ordering for the packet-artifact integrity chain

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-17

``packet_artifact`` was the only chained table without a DB-generated
monotonic sequence (``audit_event`` and ``account_audit_event`` both have
one). Chain-link order is serialized by ``generate_packet_op``'s per-case
transaction advisory lock, but verification and listing previously sorted by
the app-computed ``generated_at``
timestamp, captured before that lock is acquired. Two concurrent packet
generations for the same case could commit in one order while carrying
``generated_at`` values in the other order, making a genuinely untampered
chain fail verification. ``seq`` is assigned by Postgres at INSERT time
while the advisory lock is held, so it is the chain order of record.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A single ADD COLUMN ... GENERATED AS IDENTITY backfills existing rows
    # atomically as part of the DDL rewrite — unlike migrations 0007/0008/0012,
    # this does not fire the append-only trigger's BEFORE UPDATE (verified: it
    # is not a logical UPDATE) and needs no disable/enable bracketing.
    op.add_column(
        "packet_artifact",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    )
    op.create_unique_constraint("uq_packet_artifact_seq", "packet_artifact", ["seq"])
    op.create_index(
        "ix_packet_artifact_case_seq", "packet_artifact", ["case_id", "seq"]
    )


def downgrade() -> None:
    op.drop_index("ix_packet_artifact_case_seq", table_name="packet_artifact")
    op.drop_constraint("uq_packet_artifact_seq", "packet_artifact", type_="unique")
    op.drop_column("packet_artifact", "seq")
