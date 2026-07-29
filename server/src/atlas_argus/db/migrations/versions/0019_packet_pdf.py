"""store the paginated PDF alongside each packet artifact

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-29

Browser print-to-PDF produced a different document depending on who pressed
print and in which browser, which is unusable for something handed to opposing
counsel or filed with a court. The server now renders the packet itself, and
what it rendered has to be kept: the artifact record is meant to preserve
exactly what was produced, and a PDF that only ever existed in a download
stream would not be preserved at all.

Both columns are nullable, because artifacts generated before this feature
have no PDF and must not be back-filled. Re-rendering an old packet now would
manufacture a document that was never generated, served, or audited — the
opposite of what this table is for. Those rows stay honest about having only
an HTML artifact.

``packet_artifact`` is append-only, so the PDF and its hash are written by the
INSERT that creates the row; there is no later UPDATE to add them.

The corresponding change in ``integrity.packet_artifact_content`` adds
``pdfSha256`` to the hashed content *only when it is set*, so the canonical
content of every pre-existing artifact is unchanged and the packet chain keeps
verifying across this migration.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("packet_artifact", sa.Column("pdf", sa.LargeBinary(), nullable=True))
    op.add_column("packet_artifact", sa.Column("pdf_sha256", sa.String(), nullable=True))
    # A row may have neither (pre-PDF artifact) or both, never one. A PDF
    # without its hash could not be verified; a hash without its bytes would
    # claim an artifact that cannot be produced.
    op.create_check_constraint(
        "ck_packet_artifact_pdf_pairing",
        "packet_artifact",
        "(pdf IS NULL AND pdf_sha256 IS NULL) OR (pdf IS NOT NULL AND pdf_sha256 IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_packet_artifact_pdf_pairing", "packet_artifact", type_="check")
    op.drop_column("packet_artifact", "pdf_sha256")
    op.drop_column("packet_artifact", "pdf")
