"""Opening a new matter in-app.

Half of these run against a real non-owner database role rather than the test
connection. That is not incidental: ``relforcerowsecurity`` is off, so the
table owner bypasses row-level security entirely, and the suite connects as
the owner. A matter-creation test written the ordinary way would pass while
proving nothing about the environment this code actually runs in — the whole
reason matter creation needed a database function in the first place is a
policy the owner never sees.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import DBAPIError

from atlas_argus.db.migrate import grant_runtime_privileges
from atlas_argus.db.session import SessionLocal
from conftest import TEST_DATABASE_URL, login_as

RUNTIME_ROLE = "atlas_intake_runtime"
RUNTIME_PASSWORD = "atlasintakeruntimepassword"

SENIOR = "rev-okafor"  # global Senior Aviation Counsel
NON_SENIOR = "rev-natarajan"  # Claims Reviewer


def _runtime_url() -> str:
    return (
        make_url(TEST_DATABASE_URL)
        .set(username=RUNTIME_ROLE, password=RUNTIME_PASSWORD)
        .render_as_string(hide_password=False)
    )


@pytest.fixture
def runtime_engine():
    """A connection with the privileges production actually runs with."""
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
                    f"ALTER ROLE {RUNTIME_ROLE} LOGIN PASSWORD '{RUNTIME_PASSWORD}' "
                    f"NOSUPERUSER NOBYPASSRLS"
                )
            )
    finally:
        engine.dispose()
    grant_runtime_privileges(TEST_DATABASE_URL, _runtime_url())

    engine = create_engine(_runtime_url(), pool_pre_ping=True)
    yield engine
    engine.dispose()


# ── the constraint that forced this design ─────────────────────────────────


def test_runtime_role_cannot_create_a_matter_with_plain_sql(runtime_engine):
    """The premise. RLS on case_file requires an active membership in a matter
    that does not exist yet, so ordinary INSERT is impossible for the role the
    API actually uses."""
    with runtime_engine.connect() as conn:
        transaction = conn.begin()
        conn.execute(text("SELECT set_config('atlas_argus.reviewer_id', :r, true)"), {"r": SENIOR})
        with pytest.raises(DBAPIError, match="row-level security"):
            conn.execute(
                text(
                    "INSERT INTO case_file VALUES ('case-manual','X','A320','2020-01-01',"
                    "'LOC','wrongful_death','open','D-1')"
                )
            )
        transaction.rollback()


def test_runtime_role_can_create_a_matter_through_the_function(runtime_engine):
    with runtime_engine.connect() as conn:
        transaction = conn.begin()
        conn.execute(text("SELECT set_config('atlas_argus.reviewer_id', :r, true)"), {"r": SENIOR})
        case_id = conn.execute(
            text(
                "SELECT atlas_argus_create_matter('New Matter','B737','2021-03-04',"
                "'Denver','wrongful_death','open','D-9')"
            )
        ).scalar_one()
        assert case_id.startswith("case-")
        # Visible to its creator immediately, because the membership was
        # created in the same statement.
        assert conn.execute(
            text("SELECT count(*) FROM case_file WHERE id = :c"), {"c": case_id}
        ).scalar_one() == 1
        transaction.rollback()


def test_function_refuses_a_non_senior_actor(runtime_engine):
    """Defence in depth: the service layer checks this too, but a direct call
    must not be able to skip it."""
    with runtime_engine.connect() as conn:
        transaction = conn.begin()
        conn.execute(
            text("SELECT set_config('atlas_argus.reviewer_id', :r, true)"), {"r": NON_SENIOR}
        )
        with pytest.raises(DBAPIError, match="Senior Aviation Counsel"):
            conn.execute(
                text(
                    "SELECT atlas_argus_create_matter('X','B737','2021-03-04','D',"
                    "'wrongful_death','open','D-9')"
                )
            )
        transaction.rollback()


def test_function_refuses_without_a_reviewer_context(runtime_engine):
    with runtime_engine.connect() as conn:
        transaction = conn.begin()
        with pytest.raises(DBAPIError, match="no reviewer context"):
            conn.execute(
                text(
                    "SELECT atlas_argus_create_matter('X','B737','2021-03-04','D',"
                    "'wrongful_death','open','D-9')"
                )
            )
        transaction.rollback()


def test_function_cannot_be_aimed_at_an_existing_matter(runtime_engine):
    """The property the whole boundary rests on: there is no id parameter, so
    a caller cannot use this to attach itself to a matter it is not in."""
    with runtime_engine.connect() as conn:
        transaction = conn.begin()
        conn.execute(text("SELECT set_config('atlas_argus.reviewer_id', :r, true)"), {"r": SENIOR})
        first = conn.execute(
            text(
                "SELECT atlas_argus_create_matter('A','B737','2021-03-04','D',"
                "'wrongful_death','open','D-1')"
            )
        ).scalar_one()
        second = conn.execute(
            text(
                "SELECT atlas_argus_create_matter('B','B737','2021-03-04','D',"
                "'wrongful_death','open','D-2')"
            )
        ).scalar_one()
        assert first != second
        transaction.rollback()


def test_a_new_matter_is_never_created_stranded(runtime_engine):
    """A matter with no active member cannot be reached through the API at
    all; recovering one needs owner credentials. So creation must produce the
    membership atomically, not as a follow-up write that could fail."""
    with runtime_engine.connect() as conn:
        transaction = conn.begin()
        conn.execute(text("SELECT set_config('atlas_argus.reviewer_id', :r, true)"), {"r": SENIOR})
        case_id = conn.execute(
            text(
                "SELECT atlas_argus_create_matter('C','B737','2021-03-04','D',"
                "'wrongful_death','open','D-3')"
            )
        ).scalar_one()
        members = conn.execute(
            text(
                "SELECT reviewer_id, role, is_active FROM case_member WHERE case_id = :c"
            ),
            {"c": case_id},
        ).all()
        assert members == [(SENIOR, "Senior Aviation Counsel", True)]
        transaction.rollback()


# ── the API ────────────────────────────────────────────────────────────────


def _matter_body(**overrides) -> dict:
    body = {
        "name": "Trans-Pacific 118 Runway Excursion",
        "aircraft": "Boeing 777-300ER (N881TP)",
        "accidentDate": "2025-11-02",
        "location": "Anchorage, Alaska",
        "matterType": "wrongful_death",
        "docketRef": "DCA26MA004",
    }
    body.update(overrides)
    return body


def test_senior_counsel_can_open_a_matter(client):
    login_as(client, "mokafor")
    response = client.post("/api/cases", json=_matter_body())
    assert response.status_code == 201, response.text
    payload = response.json()

    assert payload["caseFile"]["name"] == "Trans-Pacific 118 Runway Excursion"
    assert payload["caseFile"]["status"] == "open"
    assert payload["caseMembership"]["role"] == "Senior Aviation Counsel"
    assert payload["caseMembership"]["isActive"] is True
    assert payload["auditEvent"]["action"] == "matter opened"


def test_the_new_matter_appears_in_the_reviewers_matter_list(client):
    login_as(client, "mokafor")
    created = client.post("/api/cases", json=_matter_body()).json()["caseFile"]["id"]
    listed = client.get("/api/cases").json()
    assert created in [entry["caseFile"]["id"] for entry in listed["cases"]]


def test_the_new_matter_is_immediately_usable(client):
    """Opening a matter you then cannot work in would be pointless."""
    login_as(client, "mokafor")
    case_id = client.post("/api/cases", json=_matter_body()).json()["caseFile"]["id"]
    response = client.get(f"/api/cases/{case_id}")
    assert response.status_code == 200
    assert response.json()["caseFile"]["id"] == case_id


def test_the_new_matter_starts_with_a_rooted_audit_chain(client):
    login_as(client, "mokafor")
    case_id = client.post("/api/cases", json=_matter_body()).json()["caseFile"]["id"]
    verification = client.get(f"/api/cases/{case_id}/audit/verify").json()
    assert verification["ok"] is True
    assert verification["audit"]["ok"] is True


def test_matter_ids_are_server_generated(client):
    """A client-supplied id would be a way to target an existing matter."""
    login_as(client, "mokafor")
    response = client.post("/api/cases", json=_matter_body(id="case-3407"))
    assert response.status_code == 201
    assert response.json()["caseFile"]["id"] != "case-3407"


def test_a_non_senior_reviewer_cannot_open_a_matter(client):
    login_as(client, "pnatarajan")
    response = client.post("/api/cases", json=_matter_body())
    assert response.status_code == 403


def test_opening_a_matter_requires_authentication(client):
    assert client.post("/api/cases", json=_matter_body()).status_code in (401, 403)


def test_opening_a_matter_requires_verified_mfa(client):
    """Same bar as the other administrative acts."""
    response = client.post("/api/auth/login", json={"username": "mokafor", "password": "argus-demo"})
    assert response.status_code == 200
    # Logged in but MFA not verified for this session.
    assert client.post("/api/cases", json=_matter_body()).status_code in (401, 403)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "   "),
        ("aircraft", ""),
        ("matterType", "not_a_real_type"),
        ("accidentDate", "02-11-2025"),
        ("accidentDate", "2025-13-45"),
        ("accidentDate", "2025-02-30"),
        ("accidentDate", "2999-01-01"),
        ("docketRef", ""),
    ],
)
def test_invalid_matter_input_is_rejected(client, field, value):
    login_as(client, "mokafor")
    response = client.post("/api/cases", json=_matter_body(**{field: value}))
    assert response.status_code == 422, f"{field}={value!r} was accepted"


def test_a_second_matter_does_not_disturb_the_first(client):
    """Matter isolation is the product's core promise; creating one must not
    grant visibility into another."""
    login_as(client, "mokafor")
    first = client.post("/api/cases", json=_matter_body()).json()["caseFile"]["id"]
    second = client.post(
        "/api/cases", json=_matter_body(name="Second", docketRef="DCA26MA005")
    ).json()["caseFile"]["id"]
    assert first != second

    with SessionLocal() as session:
        for case_id in (first, second):
            sources = session.execute(
                text("SELECT count(*) FROM source_document WHERE case_id = :c"), {"c": case_id}
            ).scalar_one()
            assert sources == 0, "a new matter must start empty"


def test_a_new_matter_starts_with_no_evidence(client):
    """It must not inherit the sample matter's contents."""
    login_as(client, "mokafor")
    case_id = client.post("/api/cases", json=_matter_body()).json()["caseFile"]["id"]
    state = client.get(f"/api/cases/{case_id}").json()
    assert state["claims"] == []
    assert state["conflicts"] == []
    assert state["sources"] == []
