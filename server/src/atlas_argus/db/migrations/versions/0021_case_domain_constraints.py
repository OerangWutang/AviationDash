"""enforce canonical values for newly written matters

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-29

The constraints are initially NOT VALID so a deployment containing historical
free-text matter labels can upgrade without rewriting its record. PostgreSQL
still enforces a NOT VALID check for every new or updated row.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Frozen copies of the trigger definitions on either side of this revision.
# Importing application helpers here would make a historical migration change
# whenever the runtime guard is strengthened later.
PREDECESSOR_CLAIM_VERIFICATION_FIELD_GUARD_SQL = """
CREATE OR REPLACE FUNCTION forbid_unmanaged_claim_verification_write() RETURNS trigger AS $$
BEGIN
    IF (NEW.quote_verification IS DISTINCT FROM OLD.quote_verification
        OR NEW.quote_verification_basis_sha256 IS DISTINCT FROM OLD.quote_verification_basis_sha256
        OR NEW.source_page_extraction_id IS DISTINCT FROM OLD.source_page_extraction_id)
       AND coalesce(current_setting('atlas_argus.verification_write', true), '') <> '1' THEN
        RAISE EXCEPTION
            'claim quote verification fields are server-controlled: '
            'route this write through the verification helper';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_claim_verification_fields_managed ON claim;
CREATE TRIGGER trg_claim_verification_fields_managed
    BEFORE UPDATE ON claim
    FOR EACH ROW EXECUTE FUNCTION forbid_unmanaged_claim_verification_write();
"""

UPGRADED_CLAIM_VERIFICATION_FIELD_GUARD_SQL = """
CREATE OR REPLACE FUNCTION forbid_unmanaged_claim_verification_write() RETURNS trigger AS $$
BEGIN
    IF (NEW.quote IS DISTINCT FROM OLD.quote
        OR NEW.page_ref IS DISTINCT FROM OLD.page_ref
        OR NEW.source_document_id IS DISTINCT FROM OLD.source_document_id
        OR NEW.quote_verification IS DISTINCT FROM OLD.quote_verification
        OR NEW.quote_verification_basis_sha256 IS DISTINCT FROM OLD.quote_verification_basis_sha256
        OR NEW.source_page_extraction_id IS DISTINCT FROM OLD.source_page_extraction_id)
       AND coalesce(current_setting('atlas_argus.verification_write', true), '') <> '1' THEN
        RAISE EXCEPTION
            'claim quote verification fields are server-controlled: '
            'route this write through the verification helper';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_claim_verification_fields_managed ON claim;
CREATE TRIGGER trg_claim_verification_fields_managed
    BEFORE UPDATE ON claim
    FOR EACH ROW EXECUTE FUNCTION forbid_unmanaged_claim_verification_write();
"""


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE case_file
        ADD CONSTRAINT ck_case_matter_type
        CHECK (matter_type IN (
            'wrongful_death',
            'personal_injury',
            'property_damage',
            'subrogation',
            'regulatory_enforcement',
            'insurance_coverage'
        )) NOT VALID
        """
    )
    op.execute(
        """
        ALTER TABLE case_file
        ADD CONSTRAINT ck_case_status
        CHECK (status = 'open') NOT VALID
        """
    )
    # Replace 0017's trigger so changes to verification inputs are managed as
    # strictly as changes to the derived verification result.
    op.execute(UPGRADED_CLAIM_VERIFICATION_FIELD_GUARD_SQL)


def downgrade() -> None:
    op.drop_constraint("ck_case_status", "case_file", type_="check")
    op.drop_constraint("ck_case_matter_type", "case_file", type_="check")
    op.execute(PREDECESSOR_CLAIM_VERIFICATION_FIELD_GUARD_SQL)
