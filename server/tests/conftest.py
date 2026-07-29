"""Test harness: real Postgres (docker compose `db`), dedicated test database,
schema + guards installed once, sample case reseeded before every test."""

from __future__ import annotations

import os
from datetime import UTC, datetime

TEST_DATABASE_URL = os.environ.get(
    "ATLAS_ARGUS_TEST_DATABASE_URL",
    "postgresql+psycopg://atlas:atlas@localhost:5434/atlas_argus_test",
)
os.environ["ATLAS_ARGUS_DATABASE_URL"] = TEST_DATABASE_URL

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402


def _ensure_test_database() -> None:
    base, _, dbname = TEST_DATABASE_URL.rpartition("/")
    admin = create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": dbname}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    admin.dispose()


_ensure_test_database()

from atlas_argus.api.app import app  # noqa: E402
from atlas_argus.db.migrate import drop_everything, upgrade_to_head  # noqa: E402
from atlas_argus.db.seed import reset_and_seed  # noqa: E402
from atlas_argus.db.session import SessionLocal, engine  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    # Rebuild the test schema THROUGH the migrations every run — the whole
    # suite then doubles as proof that the migration path produces a schema
    # the application actually works against.
    drop_everything(engine)
    upgrade_to_head()
    yield


def login_as(client: TestClient, username: str = "mokafor") -> None:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": "argus-demo"}
    )
    assert response.status_code == 200, response.text
    token = client.cookies.get("atlas_session")
    assert token is not None
    from atlas_argus import auth
    from atlas_argus.db.models import AuthSession, Reviewer

    with SessionLocal() as session, session.begin():
        reviewer = session.execute(
            select(Reviewer).where(Reviewer.username == username)
        ).scalar_one()
        reviewer.mfa_secret = reviewer.mfa_secret or auth.generate_mfa_secret()
        reviewer.mfa_enabled_at = reviewer.mfa_enabled_at or datetime.now(UTC)
        auth_session = session.get(AuthSession, auth._token_hash(token))
        assert auth_session is not None
        auth_session.mfa_verified_at = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _fresh_case(_schema):
    from sqlalchemy import text

    from atlas_argus.auth import reset_login_throttle

    with SessionLocal() as session, session.begin():
        # Full isolation: reviewers (passwords, flags) and sessions reset per
        # test — unlike the product reset, which preserves them by design.
        session.execute(text("TRUNCATE reviewer, login_throttle CASCADE"))
        reset_and_seed(session)
    reset_login_throttle()
    yield


@pytest.fixture
def session():
    with SessionLocal() as s:
        with s.begin():
            yield s


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
