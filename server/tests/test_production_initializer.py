"""Focused tests for the one-shot production initializer."""

from __future__ import annotations

import json

import pytest
from argon2 import PasswordHasher
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from atlas_argus.db import initialize_production as initializer
from atlas_argus.db import models as m
from atlas_argus.integrity import (
    account_audit_event_content,
    audit_event_content,
    chain_sha256,
)
from conftest import TEST_DATABASE_URL

INITIAL_PASSWORD = "one-time-initial-password"


def _manifest() -> dict:
    return {
        "schemaVersion": 1,
        "matter": {
            "id": "case-production-001",
            "name": "North Sea Aviation Matter",
            "aircraft": "Airbus A320-200",
            "accidentDate": "2025-04-19",
            "location": "North Sea",
            "matterType": "wrongful_death",
            "status": "open",
            "docketRef": "MATTER-2025-001",
        },
        "initialSeniorCounsel": {
            "id": "rev-production-admin",
            "name": "Alex Morgan",
            "username": "amorgan",
            "role": "Senior Aviation Counsel",
        },
    }


def _write_manifest(tmp_path, value: dict | None = None):
    path = tmp_path / "initial-matter.json"
    path.write_text(json.dumps(_manifest() if value is None else value), encoding="utf-8")
    return path


def _empty_application_database() -> None:
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("TRUNCATE TABLE reviewer, case_file, login_throttle CASCADE")
            )
    finally:
        engine.dispose()


def _configure_production(
    monkeypatch,
    grant_calls: list[tuple[str, str]],
    upgrade_calls: list[None],
) -> None:
    monkeypatch.setenv("ATLAS_ARGUS_ENV", "production")
    monkeypatch.setenv(initializer.INITIAL_PASSWORD_ENV, INITIAL_PASSWORD)
    monkeypatch.setattr(initializer, "assert_production_config", lambda **_: None)
    monkeypatch.setattr(initializer, "migration_database_url", lambda: TEST_DATABASE_URL)
    monkeypatch.setattr(
        initializer,
        "runtime_database_url",
        lambda: "postgresql+psycopg://atlas_app:runtime-secret@localhost/atlas_argus_test",
    )
    monkeypatch.setattr(initializer, "upgrade_to_head", lambda: upgrade_calls.append(None))
    monkeypatch.setattr(
        initializer,
        "grant_runtime_privileges",
        lambda owner, runtime: grant_calls.append((owner, runtime)),
    )


def test_greenfield_initialization_creates_integrity_valid_root_records(monkeypatch, tmp_path):
    _empty_application_database()
    grants: list[tuple[str, str]] = []
    upgrades: list[None] = []
    _configure_production(monkeypatch, grants, upgrades)

    result = initializer.initialize_production(_write_manifest(tmp_path))

    assert result == initializer.InitializationResult(
        matter_id="case-production-001",
        reviewer_id="rev-production-admin",
        username="amorgan",
    )
    assert grants == [
        (
            TEST_DATABASE_URL,
            "postgresql+psycopg://atlas_app:runtime-secret@localhost/atlas_argus_test",
        )
    ]
    assert upgrades == [None]

    engine = create_engine(TEST_DATABASE_URL)
    try:
        with Session(engine) as session:
            case = session.get(m.CaseFile, result.matter_id)
            reviewer = session.get(m.Reviewer, result.reviewer_id)
            membership = session.execute(select(m.CaseMember)).scalar_one()
            account_event = session.execute(select(m.AccountAuditEvent)).scalar_one()
            case_event = session.execute(select(m.AuditEvent)).scalar_one()

            assert case is not None
            assert case.name == "North Sea Aviation Matter"
            assert reviewer is not None
            assert reviewer.role == "Senior Aviation Counsel"
            assert reviewer.must_change_password is True
            assert reviewer.mfa_secret is None
            assert PasswordHasher().verify(reviewer.password_hash, INITIAL_PASSWORD)
            assert membership.case_id == case.id
            assert membership.reviewer_id == reviewer.id
            assert membership.role == "Senior Aviation Counsel"
            assert membership.is_active is True

            assert account_event.previous_integrity_hash is None
            assert account_event.integrity_hash == chain_sha256(
                previous_hash=None,
                content=account_audit_event_content(account_event),
            )
            assert case_event.previous_integrity_hash is None
            assert case_event.integrity_hash == chain_sha256(
                previous_hash=None,
                content=audit_event_content(case_event),
            )
    finally:
        engine.dispose()


def test_second_run_refuses_without_creating_more_records(monkeypatch, tmp_path):
    _empty_application_database()
    grants: list[tuple[str, str]] = []
    upgrades: list[None] = []
    _configure_production(monkeypatch, grants, upgrades)
    path = _write_manifest(tmp_path)
    initializer.initialize_production(path)

    with pytest.raises(initializer.InitializationRefused, match="not empty"):
        initializer.initialize_production(path)

    engine = create_engine(TEST_DATABASE_URL)
    try:
        with Session(engine) as session:
            assert len(list(session.scalars(select(m.CaseFile)))) == 1
            assert len(list(session.scalars(select(m.Reviewer)))) == 1
            assert len(list(session.scalars(select(m.CaseMember)))) == 1
            assert len(list(session.scalars(select(m.AccountAuditEvent)))) == 1
            assert len(list(session.scalars(select(m.AuditEvent)))) == 1
    finally:
        engine.dispose()
    assert len(grants) == 1
    assert upgrades == [None]


def test_record_creation_failure_rolls_back_the_whole_initialization(monkeypatch, tmp_path):
    _empty_application_database()
    grants: list[tuple[str, str]] = []
    upgrades: list[None] = []
    _configure_production(monkeypatch, grants, upgrades)

    real_new_id = initializer._new_id

    def fail_after_membership(prefix: str) -> str:
        if prefix == "aae":
            raise RuntimeError("simulated audit creation failure")
        return real_new_id(prefix)

    monkeypatch.setattr(initializer, "_new_id", fail_after_membership)
    with pytest.raises(RuntimeError, match="simulated audit creation failure"):
        initializer.initialize_production(_write_manifest(tmp_path))

    engine = create_engine(TEST_DATABASE_URL)
    try:
        with Session(engine) as session:
            assert session.execute(select(m.CaseFile)).first() is None
            assert session.execute(select(m.Reviewer)).first() is None
            assert session.execute(select(m.CaseMember)).first() is None
            assert session.execute(select(m.AccountAuditEvent)).first() is None
            assert session.execute(select(m.AuditEvent)).first() is None
    finally:
        engine.dispose()


def test_initializer_refuses_development_mode_before_reading_manifest(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_ARGUS_ENV", "dev")
    monkeypatch.delenv(initializer.INITIAL_PASSWORD_ENV, raising=False)

    with pytest.raises(initializer.InitializationRefused, match="ENV=production"):
        initializer.initialize_production(tmp_path / "does-not-exist.json")


def test_initializer_refuses_invalid_manifest_before_database_work(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_ARGUS_ENV", "production")
    monkeypatch.setenv(initializer.INITIAL_PASSWORD_ENV, INITIAL_PASSWORD)
    monkeypatch.setattr(initializer, "assert_production_config", lambda **_: None)
    called = False

    def unexpected_url() -> str:
        nonlocal called
        called = True
        return TEST_DATABASE_URL

    monkeypatch.setattr(initializer, "migration_database_url", unexpected_url)
    manifest = _manifest()
    manifest["initialSeniorCounsel"]["role"] = "Claims Reviewer"

    with pytest.raises(initializer.InitializationRefused, match="Manifest validation failed"):
        initializer.initialize_production(_write_manifest(tmp_path, manifest))
    assert called is False


@pytest.mark.parametrize("password", ["", "too-short", "argus-demo", "Alex Morgan"])
def test_initializer_validates_password_before_database_work(monkeypatch, tmp_path, password):
    monkeypatch.setenv("ATLAS_ARGUS_ENV", "production")
    if password:
        monkeypatch.setenv(initializer.INITIAL_PASSWORD_ENV, password)
    else:
        monkeypatch.delenv(initializer.INITIAL_PASSWORD_ENV, raising=False)
    monkeypatch.setattr(initializer, "assert_production_config", lambda **_: None)
    called = False

    def unexpected_url() -> str:
        nonlocal called
        called = True
        return TEST_DATABASE_URL

    monkeypatch.setattr(initializer, "migration_database_url", unexpected_url)
    with pytest.raises(initializer.InitializationRefused, match="INITIAL_ADMIN_PASSWORD"):
        initializer.initialize_production(_write_manifest(tmp_path))
    assert called is False


def test_manifest_and_cli_errors_do_not_leak_initial_password(monkeypatch, tmp_path, capsys):
    leaked = "never-print-this-initial-password"
    monkeypatch.setenv("ATLAS_ARGUS_ENV", "production")
    monkeypatch.setenv(initializer.INITIAL_PASSWORD_ENV, leaked)
    monkeypatch.setattr(initializer, "assert_production_config", lambda **_: None)
    manifest = _manifest()
    manifest["initialSeniorCounsel"]["password"] = leaked
    path = _write_manifest(tmp_path, manifest)

    assert initializer.main([str(path)]) == 1
    captured = capsys.readouterr()
    assert leaked not in captured.out
    assert leaked not in captured.err
    assert "Manifest validation failed" in captured.err
