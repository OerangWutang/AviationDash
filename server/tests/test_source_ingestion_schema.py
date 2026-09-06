"""Database-level doctrine for document ingestion.

These are the guarantees the service layer cannot provide on its own: that
extracted text a claim was verified against can never be rewritten, that an
extraction run cannot be attached to a document it did not come from, that the
active-run pointer is set once rather than swapped freely, and that quote
verification cannot be relabelled by a generic UPDATE.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from atlas_argus.db.migrate import APPEND_ONLY_TABLES

CASE = "c-schema"
SOURCE = "s-schema"
OTHER_SOURCE = "s-schema-other"


@pytest.fixture
def ingested(session):
    """Two sources in one case; the first has a run with one extracted page."""
    session.execute(
        text(
            "INSERT INTO case_file VALUES "
            "(:c,'Schema case','A320','2020-01-01','LOC','wrongful_death','open','D-1')"
        ),
        {"c": CASE},
    )
    for source_id in (SOURCE, OTHER_SOURCE):
        session.execute(
            text(
                "INSERT INTO source_document "
                "(id,case_id,title,type,origin,custodian,ingested_at,sha256,"
                " privilege_status,custody) "
                "VALUES (:s,:c,'T','deposition','O','C',now(),'legacy','public','[]'::jsonb)"
            ),
            {"s": source_id, "c": CASE},
        )
    for run_id in ("run-1", "run-2"):
        session.execute(
            text(
                "INSERT INTO source_extraction_run VALUES "
                "(:r,:s,:c,now(),now(),'pypdf-x','tesseract-x','eng','{}'::jsonb,1,0,0,'mh')"
            ),
            {"r": run_id, "s": SOURCE, "c": CASE},
        )
    session.execute(
        text(
            "INSERT INTO source_page_extraction VALUES "
            "(:p,'run-1',:s,:c,1,'the stick shaker activated','native',NULL,false,NULL,NULL,'th')"
        ),
        {"p": "page-1", "s": SOURCE, "c": CASE},
    )
    # The append-only triggers are FOR EACH ROW, so every guarded table needs a
    # row for a rejected UPDATE/DELETE to have something to fire on.
    session.execute(
        text(
            "INSERT INTO source_document_file VALUES "
            "(:s,:c,'\\x25504446'::bytea,'ch','application/pdf','f.pdf',4,1,now(),NULL,NULL)"
        ),
        {"s": SOURCE, "c": CASE},
    )
    session.execute(
        text(
            "INSERT INTO claim (id,case_id,text,source_document_id,page_ref,quote,confidence,"
            "evidence_quality,status,privilege_status,report_eligibility,updated_at) VALUES "
            "('clm-seed',:c,'a claim of sufficient length',:s,'p.1','quoted text',0.5,"
            "'primary','unreviewed','public','needs_review',now())"
        ),
        {"c": CASE, "s": SOURCE},
    )
    reviewer_id = session.execute(text("SELECT id FROM reviewer LIMIT 1")).scalar_one()
    session.execute(
        text(
            "INSERT INTO claim_quote_verification "
            "(id,case_id,claim_id,reviewer_id,matter_role,at,event_type,reasoning,"
            " previous_verification,new_verification,verification_basis_sha256,"
            " source_page_extraction_id) "
            "VALUES ('cqv-seed',:c,'clm-seed',:r,'Senior Aviation Counsel',now(),"
            "'manual_verified','checked against the original exhibit copy',"
            "'manual_verification_required','manually_verified','bh','page-1')"
        ),
        {"c": CASE, "r": reviewer_id},
    )
    return {"case": CASE, "source": SOURCE, "other": OTHER_SOURCE, "page": "page-1"}


# ── append-only ────────────────────────────────────────────────────────────


def test_source_custody_metadata_is_immutable(session, ingested):
    source_id = ingested["source"]
    with pytest.raises(DBAPIError, match="custody metadata is immutable"):
        session.execute(
            text(
                "UPDATE source_document SET custodian = 'Rewritten custodian' "
                "WHERE id = :source_id"
            ),
            {"source_id": source_id},
        )



@pytest.mark.parametrize(
    ("table", "mutation"),
    [
        ("source_document_file", "UPDATE source_document_file SET mime_type = 'x'"),
        ("source_document_file", "DELETE FROM source_document_file"),
        ("source_extraction_run", "UPDATE source_extraction_run SET page_count = 99"),
        ("source_extraction_run", "DELETE FROM source_extraction_run"),
        (
            "source_page_extraction",
            "UPDATE source_page_extraction SET extracted_text = 'rewritten'",
        ),
        ("source_page_extraction", "DELETE FROM source_page_extraction"),
        ("claim_quote_verification", "UPDATE claim_quote_verification SET reasoning = 'x'"),
        ("claim_quote_verification", "DELETE FROM claim_quote_verification"),
    ],
)
def test_ingestion_tables_are_append_only(session, ingested, table, mutation):
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text(mutation))


def test_rewriting_extracted_text_is_rejected(session, ingested):
    """The point of the feature: a quote verified against this text must not be
    falsifiable by editing the text afterwards."""
    with pytest.raises(DBAPIError, match="source_page_extraction is append-only"):
        session.execute(
            text(
                "UPDATE source_page_extraction SET extracted_text = 'the stick shaker did not "
                "activate' WHERE id = :p"
            ),
            {"p": ingested["page"]},
        )


def test_append_only_grant_list_covers_every_guarded_table(session):
    """``migrate.py`` revokes UPDATE/DELETE from the runtime role using a
    hardcoded list. A table with a trigger but no revoke silently keeps a
    privilege it should not have, and nothing else in the suite would notice."""
    guarded = set(
        session.execute(
            text(
                "SELECT DISTINCT c.relname "
                "  FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                " WHERE NOT t.tgisinternal AND t.tgname LIKE '%append_only%'"
            )
        ).scalars()
    )
    assert guarded, "expected append-only triggers to exist"
    assert guarded <= set(APPEND_ONLY_TABLES), (
        f"tables have an append-only trigger but are missing from "
        f"migrate.APPEND_ONLY_TABLES: {sorted(guarded - set(APPEND_ONLY_TABLES))}"
    )


# ── same-source composite references ───────────────────────────────────────


def test_page_cannot_reference_a_run_from_another_source(session, ingested):
    """run-1 belongs to SOURCE; claiming it for OTHER_SOURCE must fail in the
    schema, not merely in Python."""
    with pytest.raises(IntegrityError, match="fk_source_page_run_same_source"):
        session.execute(
            text(
                "INSERT INTO source_page_extraction VALUES "
                "('page-x','run-1',:other,:c,2,'t','native',NULL,false,NULL,NULL,'h')"
            ),
            {"other": ingested["other"], "c": ingested["case"]},
        )


def test_active_run_cannot_point_at_another_sources_run(session, ingested):
    with pytest.raises(IntegrityError, match="fk_source_active_run_same_source"):
        session.execute(
            text("UPDATE source_document SET active_extraction_run_id='run-1' WHERE id=:other"),
            {"other": ingested["other"]},
        )


def test_claim_cannot_cite_a_page_of_a_different_source(session, ingested):
    """A claim on OTHER_SOURCE must not be able to cite SOURCE's page. Checked
    on INSERT: an UPDATE would be stopped earlier by the verification-field
    guard (see ``test_generic_update_cannot_repoint_a_claims_page``), so this
    exercises the foreign key itself."""
    with pytest.raises(IntegrityError, match="fk_claim_page_same_source"):
        session.execute(
            text(
                "INSERT INTO claim (id,case_id,text,source_document_id,"
                "source_page_extraction_id,page_ref,quote,confidence,evidence_quality,status,"
                "privilege_status,report_eligibility,updated_at) VALUES "
                "('clm-x',:c,'a claim of sufficient length',:other,:p,'p.1','quoted text',"
                "0.5,'primary','unreviewed','public','needs_review',now())"
            ),
            {"c": ingested["case"], "other": ingested["other"], "p": ingested["page"]},
        )


def test_generic_update_cannot_repoint_a_claims_page(session, ingested):
    """Moving a claim to a different page changes what its quote was verified
    against, so it must go through the verification helper, not a bare UPDATE."""
    with pytest.raises(DBAPIError, match="server-controlled"):
        session.execute(
            text("UPDATE claim SET source_page_extraction_id = :p WHERE id = 'clm-seed'"),
            {"p": ingested["page"]},
        )


@pytest.mark.parametrize(
    ("statement", "value"),
    [
        ("UPDATE claim SET quote = :value WHERE id = 'clm-seed'", "A different quote"),
        ("UPDATE claim SET page_ref = :value WHERE id = 'clm-seed'", "p. 999"),
        (
            "UPDATE claim SET source_document_id = :value WHERE id = 'clm-seed'",
            "src-other",
        ),
    ],
)
def test_generic_update_cannot_change_quote_verification_inputs(
    session, ingested, statement, value
):
    with pytest.raises(DBAPIError, match="server-controlled"):
        session.execute(text(statement), {"value": value})


# ── set-once active run ────────────────────────────────────────────────────


def test_first_activation_is_allowed(session, ingested):
    session.execute(
        text("UPDATE source_document SET active_extraction_run_id='run-1' WHERE id=:s"),
        {"s": ingested["source"]},
    )
    assert session.execute(
        text("SELECT active_extraction_run_id FROM source_document WHERE id=:s"),
        {"s": ingested["source"]},
    ).scalar_one() == "run-1"


def test_switching_active_run_is_rejected(session, ingested):
    """Reprocessing changes what every existing claim was verified against, so
    it needs its own reviewed workflow rather than an UPDATE."""
    session.execute(
        text("UPDATE source_document SET active_extraction_run_id='run-1' WHERE id=:s"),
        {"s": ingested["source"]},
    )
    with pytest.raises(DBAPIError, match="set once"):
        session.execute(
            text("UPDATE source_document SET active_extraction_run_id='run-2' WHERE id=:s"),
            {"s": ingested["source"]},
        )


def test_clearing_active_run_is_rejected(session, ingested):
    session.execute(
        text("UPDATE source_document SET active_extraction_run_id='run-1' WHERE id=:s"),
        {"s": ingested["source"]},
    )
    with pytest.raises(DBAPIError, match="set once"):
        session.execute(
            text("UPDATE source_document SET active_extraction_run_id=NULL WHERE id=:s"),
            {"s": ingested["source"]},
        )


# ── server-controlled verification fields ──────────────────────────────────


def test_generic_update_cannot_relabel_quote_verification(session, ingested):
    """Without the guard, any claim-update path could stamp 'verified_exact'
    onto a quote nothing ever checked."""
    session.execute(
        text(
            "INSERT INTO claim (id,case_id,text,source_document_id,page_ref,quote,confidence,"
            "evidence_quality,status,privilege_status,report_eligibility,updated_at) VALUES "
            "('clm-v',:c,'some claim text here',:s,'p.1','quoted text',0.5,'primary',"
            "'unreviewed','public','needs_review',now())"
        ),
        {"c": ingested["case"], "s": ingested["source"]},
    )
    with pytest.raises(DBAPIError, match="server-controlled"):
        session.execute(
            text("UPDATE claim SET quote_verification='verified_exact' WHERE id='clm-v'")
        )


def test_verification_helper_flag_permits_the_write(session, ingested):
    """The same write succeeds when it announces itself as a managed
    verification write — proving the guard gates the path, not the value."""
    session.execute(
        text(
            "INSERT INTO claim (id,case_id,text,source_document_id,page_ref,quote,confidence,"
            "evidence_quality,status,privilege_status,report_eligibility,updated_at) VALUES "
            "('clm-w',:c,'some claim text here',:s,'p.1','quoted text',0.5,'primary',"
            "'unreviewed','public','needs_review',now())"
        ),
        {"c": ingested["case"], "s": ingested["source"]},
    )
    session.execute(text("SELECT set_config('atlas_argus.verification_write','1',true)"))
    session.execute(
        text("UPDATE claim SET quote_verification='verified_exact' WHERE id='clm-w'")
    )
    assert session.execute(
        text("SELECT quote_verification FROM claim WHERE id='clm-w'")
    ).scalar_one() == "verified_exact"


def test_existing_claims_default_to_legacy_unverified(session):
    """Seeded claims predate ingestion. The honest label is 'nothing checked
    this', never a verified badge."""
    states = set(
        session.execute(text("SELECT DISTINCT quote_verification FROM claim")).scalars()
    )
    assert states == {"legacy_unverified"}
