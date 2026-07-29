"""Fail-closed production configuration: the process must refuse to boot
with dev defaults rather than run quietly misconfigured."""

from __future__ import annotations

import pytest

from atlas_argus.api.app import cors_origins, create_app
from atlas_argus.db import bootstrap as bootstrap_module
from atlas_argus.config import (
    cookie_secure,
    production_config_errors,
    seed_password,
)


def test_dev_mode_has_no_guards(monkeypatch):
    monkeypatch.delenv("ATLAS_ARGUS_ENV", raising=False)
    assert production_config_errors() == []
    assert cookie_secure() is False
    assert seed_password() == "argus-demo"


def test_production_refuses_default_credentials(monkeypatch):
    monkeypatch.setenv("ATLAS_ARGUS_ENV", "production")
    monkeypatch.setenv(
        "ATLAS_ARGUS_DATABASE_URL", "postgresql+psycopg://atlas:atlas@db:5432/atlas_argus"
    )
    monkeypatch.delenv("ATLAS_ARGUS_MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.delenv("ATLAS_ARGUS_METRICS_TOKEN", raising=False)
    errors = production_config_errors()
    assert len(errors) == 2
    assert any("atlas:atlas" in e for e in errors)
    assert any("METRICS_TOKEN" in e for e in errors)
    with pytest.raises(RuntimeError, match="Refusing to start"):
        create_app()


def test_production_bootstrap_requires_distinct_migration_credentials(monkeypatch):
    monkeypatch.setenv("ATLAS_ARGUS_ENV", "production")
    runtime = "postgresql+psycopg://atlas_app:runtime-secret@db:5432/atlas_argus"
    monkeypatch.setenv("ATLAS_ARGUS_DATABASE_URL", runtime)
    monkeypatch.setenv("ATLAS_ARGUS_MIGRATION_DATABASE_URL", runtime)
    monkeypatch.setenv("ATLAS_ARGUS_METRICS_TOKEN", "metrics-token-123")
    errors = production_config_errors(require_migration_url=True)
    assert any("must be distinct" in e for e in errors)
    with pytest.raises(RuntimeError, match="must be distinct"):
        create_app()


def test_production_boots_with_proper_secrets(monkeypatch):
    monkeypatch.setenv("ATLAS_ARGUS_ENV", "production")
    monkeypatch.setenv(
        "ATLAS_ARGUS_DATABASE_URL",
        "postgresql+psycopg://atlas_app:runtime-secret@db:5432/atlas_argus",
    )
    monkeypatch.setenv(
        "ATLAS_ARGUS_MIGRATION_DATABASE_URL",
        "postgresql+psycopg://atlas_owner:migration-secret@db:5432/atlas_argus",
    )
    monkeypatch.setenv("ATLAS_ARGUS_METRICS_TOKEN", "metrics-token-123")
    assert production_config_errors() == []
    assert production_config_errors(require_migration_url=True) == []
    assert cookie_secure() is True  # always on in production
    assert cors_origins() == []  # packaged production defaults to same-origin
    assert create_app() is not None


def test_public_sample_seed_is_impossible_in_production(monkeypatch, session):
    from atlas_argus.db.seed import seed_database

    monkeypatch.setenv("ATLAS_ARGUS_ENV", "production")
    with pytest.raises(RuntimeError, match="Refusing to load the public sample case"):
        seed_database(session)


def test_production_bootstrap_readiness_uses_migration_owner(monkeypatch):
    observed: dict[str, object] = {}
    monkeypatch.setattr(bootstrap_module, "assert_production_config", lambda **_: None)
    monkeypatch.setattr(bootstrap_module, "is_production", lambda: True)
    monkeypatch.setattr(bootstrap_module, "upgrade_to_head", lambda: None)
    monkeypatch.setattr(
        bootstrap_module, "migration_database_url", lambda: "postgresql://owner/db"
    )
    monkeypatch.setattr(
        bootstrap_module, "runtime_database_url", lambda: "postgresql://runtime/db"
    )
    monkeypatch.setattr(
        bootstrap_module,
        "grant_runtime_privileges",
        lambda owner, runtime: observed.update(grant=(owner, runtime)),
    )
    monkeypatch.setattr(
        bootstrap_module,
        "_database_is_seeded",
        lambda url: observed.update(readiness_url=url) or True,
    )

    assert bootstrap_module.bootstrap() == "schema migrated to head; already seeded"
    assert observed == {
        "grant": ("postgresql://owner/db", "postgresql://runtime/db"),
        "readiness_url": "postgresql://owner/db",
    }


def test_production_bootstrap_still_fails_closed_when_owner_sees_no_case(monkeypatch):
    monkeypatch.setattr(bootstrap_module, "assert_production_config", lambda **_: None)
    monkeypatch.setattr(bootstrap_module, "is_production", lambda: True)
    monkeypatch.setattr(bootstrap_module, "upgrade_to_head", lambda: None)
    monkeypatch.setattr(
        bootstrap_module, "migration_database_url", lambda: "postgresql://owner/db"
    )
    monkeypatch.setattr(
        bootstrap_module, "runtime_database_url", lambda: "postgresql://runtime/db"
    )
    monkeypatch.setattr(bootstrap_module, "grant_runtime_privileges", lambda *_: None)
    monkeypatch.setattr(bootstrap_module, "_database_is_seeded", lambda _: False)

    with pytest.raises(RuntimeError, match="Production database is empty"):
        bootstrap_module.bootstrap()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ATLAS_ARGUS_MAX_REQUEST_BODY_BYTES", "not-a-number"),
        ("ATLAS_ARGUS_MAX_PACKET_ENTRIES", "0"),
        ("ATLAS_ARGUS_MAX_PACKET_ARTIFACT_BYTES", str(2 * 1024 * 1024 * 1024)),
    ],
)
def test_production_rejects_invalid_resource_limits(monkeypatch, name, value):
    monkeypatch.setenv("ATLAS_ARGUS_ENV", "production")
    monkeypatch.setenv(
        "ATLAS_ARGUS_DATABASE_URL",
        "postgresql+psycopg://atlas_app:runtime-secret@db:5432/atlas_argus",
    )
    monkeypatch.setenv("ATLAS_ARGUS_METRICS_TOKEN", "metrics-token-123")
    monkeypatch.setenv(name, value)

    assert any(name in error for error in production_config_errors())
    with pytest.raises(RuntimeError, match=name):
        create_app()


@pytest.mark.parametrize(
    "origins",
    ["*", "null", "http://atlas.example.test", "https://atlas.example.test/path"],
)
def test_production_rejects_unsafe_cors_origins(monkeypatch, origins):
    monkeypatch.setenv("ATLAS_ARGUS_ENV", "production")
    monkeypatch.setenv(
        "ATLAS_ARGUS_DATABASE_URL",
        "postgresql+psycopg://atlas_app:runtime-secret@db:5432/atlas_argus",
    )
    monkeypatch.setenv("ATLAS_ARGUS_METRICS_TOKEN", "metrics-token-123")
    monkeypatch.setenv("ATLAS_ARGUS_CORS_ORIGINS", origins)

    assert any("CORS_ORIGINS" in error for error in production_config_errors())
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        create_app()
