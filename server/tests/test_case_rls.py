from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from atlas_argus import services
from atlas_argus.db.migrate import grant_runtime_privileges
from atlas_argus.db.models import Reviewer
from atlas_argus.db.session import SessionLocal, set_rls_reviewer_context
from conftest import TEST_DATABASE_URL

RUNTIME_ROLE = "atlas_rls_runtime"
RUNTIME_PASSWORD = "atlasrlsruntimepassword"


def _runtime_url() -> str:
    return (
        make_url(TEST_DATABASE_URL)
        .set(username=RUNTIME_ROLE, password=RUNTIME_PASSWORD)
        .render_as_string(hide_password=False)
    )


def _ensure_runtime_role() -> None:
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}'
                        ) THEN
                            CREATE ROLE {RUNTIME_ROLE} LOGIN PASSWORD '{RUNTIME_PASSWORD}';
                        END IF;
                    END
                    $$;
                    """
                )
            )
            conn.execute(
                text(
                    f"ALTER ROLE {RUNTIME_ROLE} LOGIN PASSWORD "
                    f"'{RUNTIME_PASSWORD}' NOSUPERUSER NOBYPASSRLS"
                )
            )
    finally:
        engine.dispose()
    grant_runtime_privileges(TEST_DATABASE_URL, _runtime_url())


def _insert_other_case(*, member_reviewer_id: str | None = None) -> None:
    with SessionLocal() as session, session.begin():
        session.execute(
            text(
                """
                INSERT INTO case_file
                  (id, name, aircraft, accident_date, location, matter_type, status, docket_ref)
                VALUES
                  ('case-other', 'Other Matter', 'Q400', '2026-01-01', 'Other',
                   'wrongful_death', 'open', 'OTHER')
                """
            )
        )
        if member_reviewer_id is not None:
            session.execute(
                text(
                    """
                    INSERT INTO case_member
                      (id, case_id, reviewer_id, role, is_active)
                    VALUES
                      ('cm-case-other-' || :reviewer_id, 'case-other', :reviewer_id,
                       'Senior Aviation Counsel', true)
                    """
                ),
                {"reviewer_id": member_reviewer_id},
            )
        session.execute(
            text(
                """
                INSERT INTO source_document
                  (id, case_id, title, type, origin, custodian, docket_ref, ingested_at,
                   sha256, privilege_status, custody)
                VALUES
                  ('src-other', 'case-other', 'Other Source', 'official_report', 'Other',
                   'Other', NULL, now(), repeat('a', 64), 'public', '[]'::jsonb)
                """
            )
        )


def test_runtime_role_sees_only_active_member_cases():
    _ensure_runtime_role()
    _insert_other_case()
    engine = create_engine(_runtime_url(), pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            assert conn.execute(text("SELECT count(*) FROM source_document")).scalar_one() == 0

            conn.execute(
                text("SELECT set_config('atlas_argus.reviewer_id', 'rev-okafor', true)")
            )
            visible_cases = set(
                conn.execute(text("SELECT id FROM case_file ORDER BY id")).scalars()
            )
            assert visible_cases == {"case-3407"}
            assert (
                conn.execute(
                    text("SELECT count(*) FROM source_document WHERE case_id = 'case-other'")
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


def test_runtime_role_can_see_new_case_after_membership():
    _ensure_runtime_role()
    _insert_other_case(member_reviewer_id="rev-okafor")
    engine = create_engine(_runtime_url(), pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('atlas_argus.reviewer_id', 'rev-okafor', true)")
            )
            visible_cases = set(
                conn.execute(text("SELECT id FROM case_file ORDER BY id")).scalars()
            )
            assert visible_cases == {"case-3407", "case-other"}
            assert (
                conn.execute(
                    text("SELECT count(*) FROM source_document WHERE case_id = 'case-other'")
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_runtime_role_cannot_insert_unmembered_case_evidence():
    _ensure_runtime_role()
    _insert_other_case()
    engine = create_engine(_runtime_url(), pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('atlas_argus.reviewer_id', 'rev-okafor', true)")
            )
            try:
                conn.execute(
                    text(
                        """
                        INSERT INTO source_document
                          (id, case_id, title, type, origin, custodian, docket_ref,
                           ingested_at, sha256, privilege_status, custody)
                        VALUES
                          ('src-blocked', 'case-other', 'Blocked Source',
                           'official_report', 'Other', 'Other', NULL, now(),
                           repeat('b', 64), 'public', '[]'::jsonb)
                        """
                    )
                )
            except DBAPIError as exc:
                assert "row-level security policy" in str(exc.orig)
            else:
                raise AssertionError("RLS allowed insert into an unmembered case")
    finally:
        engine.dispose()


def test_runtime_role_can_append_all_integrity_chains_without_table_locks():
    """The API role has no UPDATE/DELETE privilege on immutable tables.

    Advisory locks must still serialize every append path; the former explicit
    table locks required privileges this role intentionally does not have.
    """
    _ensure_runtime_role()
    engine = create_engine(_runtime_url(), pool_pre_ping=True)
    RuntimeSession = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with RuntimeSession() as session, session.begin():
            set_rls_reviewer_context(session, "rev-okafor")
            reviewer = session.get(Reviewer, "rev-okafor")
            assert reviewer is not None

            claim_result = services.create_claim_op(
                session,
                reviewer,
                source_document_id="src-fdr",
                text_="A runtime-role extraction used to verify append serialization.",
                page_ref="RLS-1",
                quote="Recorded parameters remained within the expected envelope.",
                confidence=0.9,
                evidence_quality="primary",
                case_id="case-3407",
            )
            assert claim_result["auditEvent"]["id"]

            decision_result = services.apply_decision_op(
                session,
                reviewer,
                "cf-1",
                decision_type="accept_claim_a",
                selected_claim_id=None,
                reasoning="The flight-recorder evidence is the most reliable account.",
                expected_version=1,
                case_id="case-3407",
            )
            assert decision_result["decision"]["id"]

            section_result = services.save_section_op(
                session,
                reviewer,
                title="Runtime Role Integrity",
                paragraph_ref="RLS-LOCK-1",
                text_=(
                    "This complete paragraph verifies that report revision appends "
                    "work under the restricted runtime database role."
                ),
                claim_ids=["clm-a4"],
                case_id="case-3407",
            )
            section_id = section_result["section"]["id"]
            services.approve_section_op(
                session,
                reviewer,
                section_id,
                expected_version=section_result["section"]["version"],
                case_id="case-3407",
            )

            packet_result = services.generate_packet_op(
                session, reviewer, packet_type="production", case_id="case-3407"
            )
            assert packet_result["packetIntegrityHash"]

            account_result = services.create_reviewer_admin(
                session,
                reviewer,
                name="Runtime Lock Reviewer",
                username="runtime-lock-reviewer",
                role="Claims Reviewer",
                initial_password="temporary-password",
            )
            assert account_result["accountAuditEvent"]["id"]
    finally:
        engine.dispose()


def test_runtime_grants_reject_the_migration_owner_role():
    with pytest.raises(RuntimeError, match="distinct from the migration-owner"):
        grant_runtime_privileges(TEST_DATABASE_URL, TEST_DATABASE_URL)


def test_runtime_grants_reject_a_missing_role():
    missing_url = (
        make_url(TEST_DATABASE_URL)
        .set(username="atlas_missing_runtime_role", password="unused")
        .render_as_string(hide_password=False)
    )
    with pytest.raises(RuntimeError, match="does not exist"):
        grant_runtime_privileges(TEST_DATABASE_URL, missing_url)


@pytest.mark.parametrize(
    ("role_name", "attributes", "message"),
    [
        ("atlas_super_runtime_probe", "SUPERUSER NOBYPASSRLS", "SUPERUSER"),
        ("atlas_bypass_runtime_probe", "NOSUPERUSER BYPASSRLS", "BYPASSRLS"),
    ],
)
def test_runtime_grants_reject_unsafe_role_attributes(role_name, attributes, message):
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    role_url = (
        make_url(TEST_DATABASE_URL)
        .set(username=role_name, password="unused")
        .render_as_string(hide_password=False)
    )
    try:
        with engine.begin() as conn:
            conn.execute(text(f"DROP ROLE IF EXISTS {role_name}"))
            conn.execute(text(f"CREATE ROLE {role_name} LOGIN {attributes}"))
        with pytest.raises(RuntimeError, match=message):
            grant_runtime_privileges(TEST_DATABASE_URL, role_url)
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP ROLE IF EXISTS {role_name}"))
        engine.dispose()


def test_runtime_grants_reject_role_that_owns_public_tables():
    role_name = "atlas_table_owner_runtime_probe"
    table_name = "runtime_owned_table_probe"
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    role_url = (
        make_url(TEST_DATABASE_URL)
        .set(username=role_name, password="unused")
        .render_as_string(hide_password=False)
    )
    try:
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            conn.execute(text(f"DROP ROLE IF EXISTS {role_name}"))
            conn.execute(text(f"CREATE ROLE {role_name} LOGIN NOSUPERUSER NOBYPASSRLS"))
            conn.execute(text(f"CREATE TABLE {table_name} (id integer)"))
            conn.execute(text(f"ALTER TABLE {table_name} OWNER TO {role_name}"))
        with pytest.raises(RuntimeError, match="must not own public application tables"):
            grant_runtime_privileges(TEST_DATABASE_URL, role_url)
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            conn.execute(text(f"DROP ROLE IF EXISTS {role_name}"))
        engine.dispose()
