"""Backfill migrations populate append-only evidentiary tables that already
contain deployed data. Report revisions, audits, packets, and review decisions carry
append-only triggers installed by earlier migrations. Every other test in this
suite rebuilds the schema from an empty database (see conftest._schema),
which never exercises those backfill UPDATEs against a populated table — the
exact gap that let all three migrations ship unable to run against any real,
previously-deployed database. This test upgrades through a database that
already has rows in the guarded tables, the way a real deployment would.
"""
from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from atlas_argus.db.migrate import alembic_config, drop_everything

ADMIN_URL = "postgresql+psycopg://atlas:atlas@localhost:5434/postgres"
TEST_DB_NAME = "atlas_argus_migration_backfill_test"
TEST_DB_URL = f"postgresql+psycopg://atlas:atlas@localhost:5434/{TEST_DB_NAME}"


def test_backfill_migrations_succeed_against_a_populated_database(monkeypatch):
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
            conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    finally:
        admin.dispose()

    # env.py re-reads these on every alembic command invocation, so pointing
    # them at a throwaway database is enough to redirect the migration run —
    # it does not touch the main test database bound in db.session at import.
    monkeypatch.setenv("ATLAS_ARGUS_DATABASE_URL", TEST_DB_URL)
    monkeypatch.delenv("ATLAS_ARGUS_MIGRATION_DATABASE_URL", raising=False)
    engine = create_engine(TEST_DB_URL)
    cfg = alembic_config()

    try:
        command.upgrade(cfg, "0006")

        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO case_file (id, name, aircraft, accident_date, location, matter_type, status, docket_ref)
                VALUES ('cf-1', 'Test Case', 'Test Aircraft', '2026-01-01', 'Test Location', 'wrongful_death', 'active', 'DOCKET-1')
            """))
            conn.execute(text("""
                INSERT INTO reviewer (id, name, role, username, password_hash)
                VALUES ('rev-1', 'Test Reviewer', 'Senior Aviation Counsel', 'testreviewer', 'x')
            """))
            conn.execute(text("""
                INSERT INTO source_document
                    (id, case_id, title, type, origin, custodian, docket_ref,
                     ingested_at, sha256, privilege_status, custody)
                VALUES
                    ('src-1', 'cf-1', 'Test Source', 'official_report', 'Test',
                     'Test Custodian', NULL, now(), repeat('a', 64), 'public', '[]'::jsonb)
            """))
            conn.execute(text("""
                INSERT INTO claim
                    (id, case_id, text, source_document_id, page_ref, quote,
                     confidence, evidence_quality, status, privilege_status,
                     report_eligibility, reviewer, updated_at)
                VALUES
                    ('clm-1', 'cf-1', 'First claim', 'src-1', '1', 'First quote',
                     0.9, 'primary', 'supported', 'public', 'eligible', NULL, now()),
                    ('clm-2', 'cf-1', 'Second claim', 'src-1', '2', 'Second quote',
                     0.8, 'corroborated', 'superseded', 'public', 'excluded', NULL, now())
            """))
            conn.execute(text("""
                INSERT INTO conflict
                    (id, case_id, claim_a_id, claim_b_id, conflict_type, severity,
                     status, summary, created_at, claim_a_disposition,
                     claim_b_disposition)
                VALUES
                    ('conf-1', 'cf-1', 'clm-1', 'clm-2', 'factual', 'medium',
                     'accepted_claim_a', 'Test conflict', now(), 'supported',
                     'superseded')
            """))
            conn.execute(text("""
                INSERT INTO review_decision
                    (id, conflict_id, decision_type, selected_claim_id, reasoning,
                     reviewer_name, reviewer_role, created_at, previous_status,
                     new_status, report_impact)
                VALUES
                    ('dec-1', 'conf-1', 'accept_claim_a', 'clm-1',
                     'The first claim is supported by the primary evidence.',
                     'Test Reviewer', 'Senior Aviation Counsel', now(), 'unresolved',
                     'accepted_claim_a', 'Claim A eligible; Claim B excluded')
            """))
            conn.execute(text("""
                INSERT INTO report_section (id, case_id, title, paragraph_ref, text, claim_ids, position)
                VALUES ('sec-1', 'cf-1', 'Test Section', 'P1', 'Section text', '[]'::jsonb, 1)
            """))
            conn.execute(text("""
                INSERT INTO report_section_revision
                    (id, section_id, case_id, title, paragraph_ref, text, claim_ids,
                     author_reviewer_id, author, author_role, created_at, revision_reason,
                     parent_revision_id, approval_state)
                VALUES
                    ('rev-a', 'sec-1', 'cf-1', 'Test Section', 'P1', 'Section text', '[]'::jsonb,
                     'rev-1', 'Test Reviewer', 'Senior Aviation Counsel', now(), 'initial draft',
                     NULL, 'draft')
            """))
            conn.execute(text("""
                INSERT INTO audit_event
                    (id, case_id, at, actor, actor_role, action, subject_type, subject_id,
                     previous_status, new_status, detail, report_eligibility_changed)
                VALUES
                    ('ae-1', 'cf-1', now(), 'Test Reviewer', 'Senior Aviation Counsel', 'test_action',
                     'claim', 'clm-1', NULL, NULL, 'test detail', false)
            """))
            conn.execute(text("""
                INSERT INTO account_audit_event
                    (id, at, actor_reviewer_id, actor, actor_role, action, subject_reviewer_id,
                     previous_status, new_status, detail)
                VALUES
                    ('aae-1', now(), 'rev-1', 'Test Reviewer', 'Senior Aviation Counsel', 'test_action',
                     'rev-1', NULL, NULL, 'test detail')
            """))
            conn.execute(text("""
                INSERT INTO packet_artifact
                    (id, case_id, packet_type, generated_at, generated_by_reviewer_id,
                     generated_by_name, generated_by_role, filename, body_sha256, document, manifest)
                VALUES
                    ('pkt-1', 'cf-1', 'internal', now(), 'rev-1', 'Test Reviewer',
                     'Senior Aviation Counsel', 'packet.txt', 'deadbeef', 'doc body', '{}'::jsonb)
            """))

        # The actual regression check: 0007 (report_section_revision backfill),
        # 0008 (audit_event.actor_reviewer_id backfill), and 0012 (hash-chain
        # backfill) must not crash against these pre-existing rows.
        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT content_sha256 FROM report_section_revision WHERE id = 'rev-a'")
                ).scalar_one()
                is not None
            )
            assert (
                conn.execute(
                    text("SELECT actor_reviewer_id FROM audit_event WHERE id = 'ae-1'")
                ).scalar_one()
                == "rev-1"
            )
            for table, row_id in (
                ("audit_event", "ae-1"),
                ("account_audit_event", "aae-1"),
                ("packet_artifact", "pkt-1"),
                ("review_decision", "dec-1"),
            ):
                assert (
                    conn.execute(
                        text(f"SELECT integrity_hash FROM {table} WHERE id = :id"), {"id": row_id}
                    ).scalar_one()
                    is not None
                )

        # The append-only guarantee must still hold once the migration commits
        # — the fix suspends the trigger only for the backfill, not for good.
        with pytest.raises(Exception, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text("UPDATE audit_event SET detail = 'tampered' WHERE id = 'ae-1'"))
        with pytest.raises(Exception, match="review_decision is append-only"):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE review_decision SET reasoning = 'tampered' "
                        "WHERE id = 'dec-1'"
                    )
                )
    finally:
        drop_everything(engine)
        engine.dispose()
        admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
        admin.dispose()
