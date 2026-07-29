"""same-case relational integrity for legal evidence links

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-11
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SELECTED_CLAIM_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION enforce_decision_selected_claim() RETURNS trigger AS $$
DECLARE
    claim_a text;
    claim_b text;
BEGIN
    IF NEW.selected_claim_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT c.claim_a_id, c.claim_b_id
      INTO claim_a, claim_b
      FROM conflict c
     WHERE c.id = NEW.conflict_id;

    IF NEW.selected_claim_id <> claim_a AND NEW.selected_claim_id <> claim_b THEN
        RAISE EXCEPTION
          'review_decision selected_claim_id must be one of the conflict claims'
          USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_decision_selected_claim ON review_decision;
CREATE TRIGGER trg_decision_selected_claim
    BEFORE INSERT OR UPDATE ON review_decision
    FOR EACH ROW EXECUTE FUNCTION enforce_decision_selected_claim();
"""


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_source_document_id_case",
        "source_document",
        ["id", "case_id"],
    )
    op.create_unique_constraint("uq_claim_id_case", "claim", ["id", "case_id"])
    op.create_unique_constraint(
        "uq_report_section_id_case",
        "report_section",
        ["id", "case_id"],
    )
    op.create_foreign_key(
        "fk_claim_source_same_case",
        "claim",
        "source_document",
        ["source_document_id", "case_id"],
        ["id", "case_id"],
    )
    op.create_foreign_key(
        "fk_conflict_claim_a_same_case",
        "conflict",
        "claim",
        ["claim_a_id", "case_id"],
        ["id", "case_id"],
    )
    op.create_foreign_key(
        "fk_conflict_claim_b_same_case",
        "conflict",
        "claim",
        ["claim_b_id", "case_id"],
        ["id", "case_id"],
    )
    op.create_foreign_key(
        "fk_report_revision_section_same_case",
        "report_section_revision",
        "report_section",
        ["section_id", "case_id"],
        ["id", "case_id"],
    )
    op.execute(SELECTED_CLAIM_TRIGGER_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_decision_selected_claim ON review_decision")
    op.execute("DROP FUNCTION IF EXISTS enforce_decision_selected_claim()")
    op.drop_constraint(
        "fk_report_revision_section_same_case",
        "report_section_revision",
        type_="foreignkey",
    )
    op.drop_constraint("fk_conflict_claim_b_same_case", "conflict", type_="foreignkey")
    op.drop_constraint("fk_conflict_claim_a_same_case", "conflict", type_="foreignkey")
    op.drop_constraint("fk_claim_source_same_case", "claim", type_="foreignkey")
    op.drop_constraint(
        "uq_report_section_id_case",
        "report_section",
        type_="unique",
    )
    op.drop_constraint("uq_claim_id_case", "claim", type_="unique")
    op.drop_constraint("uq_source_document_id_case", "source_document", type_="unique")
