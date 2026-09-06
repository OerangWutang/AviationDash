"""hash-chain report section revisions

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-17

``report_section_revision.content_sha256`` proved nothing on its own: an
attacker who bypasses the append-only trigger and edits a row can always
recompute a self-consistent hash for the new content, since content_sha256
doesn't depend on any other row. Chaining off ``parent_revision_id`` (each
section's own revision lineage, already tracked) means tampering with an
older revision also invalidates every descendant's integrity_hash, and the
chain is now covered by ``verify_case_integrity`` — previously no exposed
endpoint could catch this table being tampered with at all.
"""

from datetime import UTC
import hashlib
import json
from types import SimpleNamespace
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def chain_sha256(*, previous_hash: str | None, content: Any) -> str:
    return _canonical_sha256(
        {
            "schema": "atlas_argus.integrity_chain_link.v1",
            "previousHash": previous_hash,
            "content": content,
        }
    )


def report_section_revision_content(item: Any) -> dict:
    return {
        "schema": "atlas_argus.report_section_revision_row.v1",
        "id": item.id,
        "sectionId": item.section_id,
        "caseId": item.case_id,
        "title": item.title,
        "paragraphRef": item.paragraph_ref,
        "text": item.text,
        "claimIds": list(item.claim_ids),
        "authorReviewerId": item.author_reviewer_id,
        "author": item.author,
        "authorRole": item.author_role,
        "createdAt": item.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "revisionReason": item.revision_reason,
        "parentRevisionId": item.parent_revision_id,
        "approvalState": item.approval_state,
        "contentSha256": item.content_sha256,
    }

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill_section_revisions(bind) -> None:
    previous_hash_by_id: dict[str, str | None] = {}
    rows = list(
        bind.execute(
        sa.text(
            """
            SELECT id, section_id, case_id, title, paragraph_ref, text, claim_ids,
                   author_reviewer_id, author, author_role, created_at,
                   revision_reason, parent_revision_id, approval_state, content_sha256
              FROM report_section_revision
            """
        )
        ).mappings()
    )
    remaining = {row["id"]: row for row in rows}
    while remaining:
        progressed = False
        for row_id, row in list(remaining.items()):
            parent_id = row["parent_revision_id"]
            if parent_id is not None and parent_id not in previous_hash_by_id:
                continue
            previous_hash = (
                previous_hash_by_id[parent_id] if parent_id is not None else None
            )
            revision_row = SimpleNamespace(**dict(row))
            integrity_hash = chain_sha256(
                previous_hash=previous_hash,
                content=report_section_revision_content(revision_row),
            )
            bind.execute(
                sa.text(
                    """
                    UPDATE report_section_revision
                       SET previous_integrity_hash = :previous_hash,
                           integrity_hash = :integrity_hash
                     WHERE id = :id
                    """
                ),
                {
                    "id": row_id,
                    "previous_hash": previous_hash,
                    "integrity_hash": integrity_hash,
                },
            )
            previous_hash_by_id[row_id] = integrity_hash
            del remaining[row_id]
            progressed = True
        if not progressed:
            unresolved = ", ".join(sorted(remaining))
            raise RuntimeError(
                "Cannot backfill report revision chain; cyclic or missing "
                f"parent(s) for: {unresolved}"
            )


def upgrade() -> None:
    op.add_column(
        "report_section_revision",
        sa.Column("previous_integrity_hash", sa.String(), nullable=True),
    )
    op.add_column(
        "report_section_revision",
        sa.Column("integrity_hash", sa.String(), nullable=True),
    )
    bind = op.get_bind()
    # report_section_revision is append-only (trigger installed in 0005) — the
    # trigger rejects this backfill's UPDATEs on any database that already has
    # revision rows, which is every real deployment. Suspend it for the
    # backfill only.
    op.execute("ALTER TABLE report_section_revision DISABLE TRIGGER trg_report_revision_append_only")
    _backfill_section_revisions(bind)
    op.execute("ALTER TABLE report_section_revision ENABLE TRIGGER trg_report_revision_append_only")
    op.alter_column("report_section_revision", "integrity_hash", nullable=False)


def downgrade() -> None:
    op.drop_column("report_section_revision", "integrity_hash")
    op.drop_column("report_section_revision", "previous_integrity_hash")
