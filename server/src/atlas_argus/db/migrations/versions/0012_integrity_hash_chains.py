"""integrity hash chains for audit and packet artifacts

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-12
"""

from datetime import UTC
import hashlib
import json
from types import SimpleNamespace
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


def _iso_z(value: Any) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


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


def audit_event_content(event: Any) -> dict:
    return {
        "schema": "atlas_argus.audit_event.v1",
        "id": event.id,
        "caseId": event.case_id,
        "at": _iso_z(event.at),
        "actorReviewerId": event.actor_reviewer_id,
        "actor": event.actor,
        "actorRole": event.actor_role,
        "action": event.action,
        "subjectType": event.subject_type,
        "subjectId": event.subject_id,
        "previousStatus": event.previous_status,
        "newStatus": event.new_status,
        "detail": event.detail,
        "reportEligibilityChanged": event.report_eligibility_changed,
    }


def account_audit_event_content(event: Any) -> dict:
    return {
        "schema": "atlas_argus.account_audit_event.v1",
        "id": event.id,
        "at": _iso_z(event.at),
        "actorReviewerId": event.actor_reviewer_id,
        "actor": event.actor,
        "actorRole": event.actor_role,
        "action": event.action,
        "subjectReviewerId": event.subject_reviewer_id,
        "previousStatus": event.previous_status,
        "newStatus": event.new_status,
        "detail": event.detail,
    }


def packet_artifact_content(artifact: Any) -> dict:
    document_sha256 = hashlib.sha256(artifact.document.encode("utf-8")).hexdigest()
    manifest = dict(artifact.manifest)
    manifest.pop("manifestSha256", None)
    manifest.pop("artifactSha256", None)
    manifest_sha256 = _canonical_sha256(manifest)
    artifact_sha256 = _canonical_sha256(
        {
            "schema": "atlas_argus.packet_artifact.v1",
            "documentSha256": document_sha256,
            "manifestSha256": manifest_sha256,
        }
    )
    return {
        "schema": "atlas_argus.packet_artifact_row.v1",
        "id": artifact.id,
        "caseId": artifact.case_id,
        "packetType": artifact.packet_type,
        "generatedAt": _iso_z(artifact.generated_at),
        "generatedByReviewerId": artifact.generated_by_reviewer_id,
        "generatedByName": artifact.generated_by_name,
        "generatedByRole": artifact.generated_by_role,
        "filename": artifact.filename,
        "bodySha256": artifact.body_sha256,
        "documentSha256": document_sha256,
        "manifestSha256": manifest_sha256,
        "artifactSha256": artifact_sha256,
    }

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CHAINED_TABLES = ("audit_event", "account_audit_event", "packet_artifact")


def _add_columns() -> None:
    for table in CHAINED_TABLES:
        op.add_column(
            table,
            sa.Column("previous_integrity_hash", sa.String(), nullable=True),
        )
        op.add_column(table, sa.Column("integrity_hash", sa.String(), nullable=True))


def _set_not_null() -> None:
    for table in CHAINED_TABLES:
        op.alter_column(table, "integrity_hash", nullable=False)


def _drop_columns() -> None:
    for table in reversed(CHAINED_TABLES):
        op.drop_column(table, "integrity_hash")
        op.drop_column(table, "previous_integrity_hash")


def _backfill_audit_events(bind) -> None:
    previous_hash: str | None = None
    previous_case_id: str | None = None
    rows = bind.execute(
        sa.text(
            """
            SELECT id, case_id, at, actor_reviewer_id, actor, actor_role, action,
                   subject_type, subject_id, previous_status, new_status, detail,
                   report_eligibility_changed
              FROM audit_event
             ORDER BY case_id, seq
            """
        )
    ).mappings()
    for row in rows:
        if row["case_id"] != previous_case_id:
            previous_hash = None
            previous_case_id = row["case_id"]
        event = SimpleNamespace(**dict(row))
        integrity_hash = chain_sha256(
            previous_hash=previous_hash,
            content=audit_event_content(event),
        )
        bind.execute(
            sa.text(
                """
                UPDATE audit_event
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
        previous_hash = integrity_hash


def _backfill_account_audit_events(bind) -> None:
    previous_hash: str | None = None
    rows = bind.execute(
        sa.text(
            """
            SELECT id, at, actor_reviewer_id, actor, actor_role, action,
                   subject_reviewer_id, previous_status, new_status, detail
              FROM account_audit_event
             ORDER BY seq
            """
        )
    ).mappings()
    for row in rows:
        event = SimpleNamespace(**dict(row))
        integrity_hash = chain_sha256(
            previous_hash=previous_hash,
            content=account_audit_event_content(event),
        )
        bind.execute(
            sa.text(
                """
                UPDATE account_audit_event
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
        previous_hash = integrity_hash


def _backfill_packet_artifacts(bind) -> None:
    previous_hash: str | None = None
    previous_case_id: str | None = None
    rows = bind.execute(
        sa.text(
            """
            SELECT id, case_id, packet_type, generated_at, generated_by_reviewer_id,
                   generated_by_name, generated_by_role, filename, body_sha256,
                   document, manifest
              FROM packet_artifact
             ORDER BY case_id, generated_at, id
            """
        )
    ).mappings()
    for row in rows:
        if row["case_id"] != previous_case_id:
            previous_hash = None
            previous_case_id = row["case_id"]
        artifact = SimpleNamespace(**dict(row))
        integrity_hash = chain_sha256(
            previous_hash=previous_hash,
            content=packet_artifact_content(artifact),
        )
        bind.execute(
            sa.text(
                """
                UPDATE packet_artifact
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
        previous_hash = integrity_hash


_APPEND_ONLY_TRIGGERS = {
    "audit_event": "trg_audit_append_only",
    "account_audit_event": "trg_account_audit_append_only",
    "packet_artifact": "trg_packet_artifact_append_only",
}


def upgrade() -> None:
    _add_columns()
    bind = op.get_bind()
    # All three chained tables are append-only (triggers installed in earlier
    # migrations) — they reject this backfill's UPDATEs on any database that
    # already has rows, which is every real deployment. Suspend each trigger
    # for its own backfill only; ENABLE below restores the append-only
    # guarantee before the migration commits.
    for table, trigger in _APPEND_ONLY_TRIGGERS.items():
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
    _backfill_audit_events(bind)
    _backfill_account_audit_events(bind)
    _backfill_packet_artifacts(bind)
    for table, trigger in _APPEND_ONLY_TRIGGERS.items():
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
    _set_not_null()


def downgrade() -> None:
    _drop_columns()
