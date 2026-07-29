"""Database-level doctrine the ORM cannot express.

- ``audit_event``, ``account_audit_event``, ``review_decision``,
  ``report_section_revision``, ``packet_artifact``, ``source_document_file``,
  ``source_extraction_run``, ``source_page_extraction``, and
  ``claim_quote_verification`` are append-only: triggers reject UPDATE and
  DELETE at the database, whatever code path attempts it.
- ``source_document.active_extraction_run_id`` is a set-once pointer, not a
  general mutable column: a trigger allows NULL -> first run and rejects every
  later change. Re-pointing a source at a different extraction run has to be a
  separately reviewed, MFA-gated activation workflow, not a side effect of an
  ordinary source update.
- ``claim.quote_verification`` and its basis hash are server-controlled: a
  trigger rejects any statement that changes them without first setting the
  transaction-local ``atlas_argus.verification_write`` flag, which only the
  verification helper sets. Without this, a generic claim UPDATE could relabel
  a fabricated quote as verified.
- ID sequences for human-readable identifiers (clm-11, cf-6, rpt-7) — a
  sequence is race-free where a MAX()+1 read is not.

Installed by the baseline migration (0001) and, for the ingestion tables, by
0017; this module holds the SQL.
"""

from __future__ import annotations

AUDIT_GUARD_SQL = """
CREATE OR REPLACE FUNCTION forbid_audit_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only: % rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_append_only ON audit_event;
CREATE TRIGGER trg_audit_append_only
    BEFORE UPDATE OR DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();
"""

ACCOUNT_AUDIT_GUARD_SQL = """
CREATE OR REPLACE FUNCTION forbid_account_audit_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'account_audit_event is append-only: % rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_account_audit_append_only ON account_audit_event;
CREATE TRIGGER trg_account_audit_append_only
    BEFORE UPDATE OR DELETE ON account_audit_event
    FOR EACH ROW EXECUTE FUNCTION forbid_account_audit_mutation();
"""

REVIEW_DECISION_GUARD_SQL = """
CREATE OR REPLACE FUNCTION forbid_review_decision_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'review_decision is append-only: % rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_review_decision_append_only ON review_decision;
CREATE TRIGGER trg_review_decision_append_only
    BEFORE UPDATE OR DELETE ON review_decision
    FOR EACH ROW EXECUTE FUNCTION forbid_review_decision_mutation();
"""

REPORT_REVISION_GUARD_SQL = """
CREATE OR REPLACE FUNCTION forbid_report_revision_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'report_section_revision is append-only: % rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_report_revision_append_only ON report_section_revision;
CREATE TRIGGER trg_report_revision_append_only
    BEFORE UPDATE OR DELETE ON report_section_revision
    FOR EACH ROW EXECUTE FUNCTION forbid_report_revision_mutation();
"""

PACKET_ARTIFACT_GUARD_SQL = """
CREATE OR REPLACE FUNCTION forbid_packet_artifact_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'packet_artifact is append-only: % rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_packet_artifact_append_only ON packet_artifact;
CREATE TRIGGER trg_packet_artifact_append_only
    BEFORE UPDATE OR DELETE ON packet_artifact
    FOR EACH ROW EXECUTE FUNCTION forbid_packet_artifact_mutation();
"""

SOURCE_DOCUMENT_FILE_GUARD_SQL = """
CREATE OR REPLACE FUNCTION forbid_source_document_file_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'source_document_file is append-only: % rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_source_document_file_append_only ON source_document_file;
CREATE TRIGGER trg_source_document_file_append_only
    BEFORE UPDATE OR DELETE ON source_document_file
    FOR EACH ROW EXECUTE FUNCTION forbid_source_document_file_mutation();
"""

SOURCE_EXTRACTION_RUN_GUARD_SQL = """
CREATE OR REPLACE FUNCTION forbid_source_extraction_run_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'source_extraction_run is append-only: % rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_source_extraction_run_append_only ON source_extraction_run;
CREATE TRIGGER trg_source_extraction_run_append_only
    BEFORE UPDATE OR DELETE ON source_extraction_run
    FOR EACH ROW EXECUTE FUNCTION forbid_source_extraction_run_mutation();
"""

SOURCE_PAGE_EXTRACTION_GUARD_SQL = """
CREATE OR REPLACE FUNCTION forbid_source_page_extraction_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'source_page_extraction is append-only: % rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_source_page_extraction_append_only ON source_page_extraction;
CREATE TRIGGER trg_source_page_extraction_append_only
    BEFORE UPDATE OR DELETE ON source_page_extraction
    FOR EACH ROW EXECUTE FUNCTION forbid_source_page_extraction_mutation();
"""

CLAIM_QUOTE_VERIFICATION_GUARD_SQL = """
CREATE OR REPLACE FUNCTION forbid_claim_quote_verification_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'claim_quote_verification is append-only: % rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_claim_quote_verification_append_only ON claim_quote_verification;
CREATE TRIGGER trg_claim_quote_verification_append_only
    BEFORE UPDATE OR DELETE ON claim_quote_verification
    FOR EACH ROW EXECUTE FUNCTION forbid_claim_quote_verification_mutation();
"""

#: ``active_extraction_run_id`` is set once, when ingestion first completes.
#: Reprocessing a source is a real evidentiary act — it changes what every
#: existing claim on that source was verified against — so switching the
#: pointer needs its own reviewed, MFA-gated workflow with activation history,
#: not a silent UPDATE. Until that exists, the database refuses the transition.
ACTIVE_EXTRACTION_RUN_GUARD_SQL = """
CREATE OR REPLACE FUNCTION forbid_active_extraction_run_change() RETURNS trigger AS $$
BEGIN
    IF NEW.active_extraction_run_id IS DISTINCT FROM OLD.active_extraction_run_id
       AND OLD.active_extraction_run_id IS NOT NULL THEN
        RAISE EXCEPTION
            'source_document.active_extraction_run_id is set once (% -> %): '
            'reprocessing requires a reviewed activation workflow',
            OLD.active_extraction_run_id, NEW.active_extraction_run_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_source_document_active_run_set_once ON source_document;
CREATE TRIGGER trg_source_document_active_run_set_once
    BEFORE UPDATE ON source_document
    FOR EACH ROW EXECUTE FUNCTION forbid_active_extraction_run_change();
"""

#: Quote verification is a server-computed conclusion about evidence, never a
#: client-supplied label. Any UPDATE that changes it must announce itself by
#: setting ``atlas_argus.verification_write`` for the transaction; only the
#: verification helper does that. A generic claim UPDATE that edits the quote
#: without routing through the helper is rejected rather than silently keeping
#: a stale "verified" badge on changed text.
CLAIM_VERIFICATION_FIELD_GUARD_SQL = """
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

#: Privilege-aware row visibility for the ingestion tables. ``0011``'s
#: ``atlas_argus_active_case_member`` only asks "is this reviewer in the
#: matter?", which is right for metadata but not for tables holding the actual
#: document bytes and extracted page text. This mirrors ``_source_visible_to``
#: in ``services.py``: privilege clearance (Senior Aviation Counsel in *this*
#: matter) or a non-withholding source.
SOURCE_PRIVILEGE_RLS_SQL = """
CREATE OR REPLACE FUNCTION atlas_argus_source_visible(
    target_source_id text, target_case_id text
) RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1
          FROM case_member cm
          JOIN source_document sd
            ON sd.id = target_source_id
           AND sd.case_id = target_case_id
         WHERE cm.case_id = target_case_id
           AND cm.reviewer_id = current_setting('atlas_argus.reviewer_id', true)
           AND cm.is_active
           AND (
               cm.role = 'Senior Aviation Counsel'
               OR sd.privilege_status NOT IN ('attorney_client', 'work_product', 'restricted')
           )
    )
$$ LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public;
"""

#: Tables whose rows are gated on source privilege, not just membership.
SOURCE_PRIVILEGE_SCOPED_TABLES = (
    "source_document_file",
    "source_extraction_run",
    "source_page_extraction",
)

#: Append-only tables added by 0017. Kept beside the originals so the runtime
#: REVOKE list in ``migrate.py`` and the migration stay in step.
INGESTION_APPEND_ONLY_TABLES = (
    "source_document_file",
    "source_extraction_run",
    "source_page_extraction",
    "claim_quote_verification",
)

ID_SEQUENCES = (
    "claim_id_seq",
    "conflict_id_seq",
    "section_id_seq",
    "source_id_seq",
    "extraction_run_id_seq",
)
