"""bind active report revisions and hash immutable content

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from atlas_argus.integrity import revision_content_sha256

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "report_section_revision",
        sa.Column("content_sha256", sa.String(), nullable=True),
    )

    bind = op.get_bind()
    # report_section_revision is append-only (trigger installed in 0005) — the
    # trigger rejects this backfill's UPDATEs on any database that already has
    # revision rows, which is every real deployment. Suspend it for the
    # backfill only; ENABLE below restores the append-only guarantee before
    # the migration commits.
    op.execute("ALTER TABLE report_section_revision DISABLE TRIGGER trg_report_revision_append_only")
    rows = bind.execute(
        sa.text(
            """
            SELECT id, case_id, section_id, title, paragraph_ref, text, claim_ids
            FROM report_section_revision
            """
        )
    ).mappings()
    for row in rows:
        bind.execute(
            sa.text(
                """
                UPDATE report_section_revision
                SET content_sha256 = :content_sha256
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "content_sha256": revision_content_sha256(
                    case_id=row["case_id"],
                    section_id=row["section_id"],
                    title=row["title"],
                    paragraph_ref=row["paragraph_ref"],
                    text=row["text"],
                    claim_ids=list(row["claim_ids"]),
                ),
            },
        )
    op.execute("ALTER TABLE report_section_revision ENABLE TRIGGER trg_report_revision_append_only")

    op.create_check_constraint(
        "ck_report_revision_approved_hash",
        "report_section_revision",
        "approval_state <> 'approved' OR content_sha256 IS NOT NULL",
    )
    op.create_unique_constraint(
        "uq_report_revision_id_section",
        "report_section_revision",
        ["id", "section_id"],
    )
    op.execute(
        """
        UPDATE report_section s
        SET active_revision_id = NULL
        WHERE active_revision_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1
            FROM report_section_revision r
            WHERE r.id = s.active_revision_id
              AND r.section_id = s.id
          )
        """
    )
    op.create_foreign_key(
        "fk_report_section_active_revision_same_section",
        "report_section",
        "report_section_revision",
        ["active_revision_id", "id"],
        ["id", "section_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_report_section_active_revision_same_section",
        "report_section",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_report_revision_id_section",
        "report_section_revision",
        type_="unique",
    )
    op.drop_constraint(
        "ck_report_revision_approved_hash",
        "report_section_revision",
        type_="check",
    )
    op.drop_column("report_section_revision", "content_sha256")
