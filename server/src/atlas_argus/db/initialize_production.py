"""One-shot initializer for a new production database.

Run only while the API is stopped::

    python -m atlas_argus.db.initialize_production /run/secrets/initial-matter.json

The initial Senior Aviation Counsel password is read exclusively from
``ATLAS_ARGUS_INITIAL_ADMIN_PASSWORD``.  It is deliberately absent from the
manifest and command-line interface.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from pydantic.functional_validators import field_validator
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session

from .. import auth
from ..domain import matters as matter_rules
from ..domain.types import ValidationFailure
from ..config import (
    DEFAULT_SEED_PASSWORD,
    assert_production_config,
    is_production,
    migration_database_url,
    runtime_database_url,
)
from ..integrity import (
    account_audit_event_content,
    audit_event_content,
    chain_sha256,
)
from . import models as m
from .migrate import grant_runtime_privileges, upgrade_to_head

INITIAL_PASSWORD_ENV = "ATLAS_ARGUS_INITIAL_ADMIN_PASSWORD"
INITIALIZER_LOCK_KEY = "atlas_argus.production_initializer.v1"
SENIOR_COUNSEL_ROLE = "Senior Aviation Counsel"
MAX_PASSWORD_LENGTH = 256

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=200),
]


class InitializationRefused(RuntimeError):
    """The initializer stopped before creating any application data."""


class _StrictManifestModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        hide_input_in_errors=True,
    )


class MatterManifest(_StrictManifestModel):
    id: Identifier
    name: ShortText
    aircraft: ShortText
    accident_date: Annotated[
        str,
        Field(alias="accidentDate", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ]
    location: ShortText
    matter_type: ShortText = Field(alias="matterType")
    status: Literal["open"]
    docket_ref: Annotated[
        str,
        Field(alias="docketRef", min_length=2, max_length=200),
    ]

    @field_validator("accident_date")
    @classmethod
    def _valid_accident_date(cls, value: str) -> str:
        try:
            return matter_rules.validate_accident_date(value)
        except ValidationFailure as exc:
            raise ValueError(str(exc)) from None

    @field_validator("matter_type")
    @classmethod
    def _valid_matter_type(cls, value: str) -> str:
        if value not in matter_rules.MATTER_TYPES:
            raise ValueError("matterType is not a supported matter type")
        return value


class InitialSeniorCounselManifest(_StrictManifestModel):
    id: Identifier
    name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=2, max_length=160),
    ]
    username: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            to_lower=True,
            min_length=3,
            max_length=128,
            pattern=r"^[a-z0-9][a-z0-9._-]*$",
        ),
    ]
    role: Literal["Senior Aviation Counsel"]


class ProductionInitializationManifest(_StrictManifestModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    matter: MatterManifest
    initial_senior_counsel: InitialSeniorCounselManifest = Field(alias="initialSeniorCounsel")


@dataclass(frozen=True)
class InitializationResult:
    matter_id: str
    reviewer_id: str
    username: str


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise InitializationRefused(f"Manifest contains a duplicate JSON key: {key!r}.")
        value[key] = item
    return value


def load_manifest(path: Path) -> ProductionInitializationManifest:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InitializationRefused(f"Cannot read initialization manifest: {exc}") from exc
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                InitializationRefused("Manifest contains a non-finite JSON number.")
            ),
        )
    except json.JSONDecodeError as exc:
        raise InitializationRefused(
            f"Manifest is not valid JSON (line {exc.lineno}, column {exc.colno})."
        ) from exc
    except InitializationRefused:
        raise
    try:
        return ProductionInitializationManifest.model_validate(value)
    except ValidationError as exc:
        # hide_input_in_errors prevents an accidentally supplied secret value
        # from being repeated into deployment logs.
        raise InitializationRefused(f"Manifest validation failed: {exc}") from exc


def _initial_password(admin: InitialSeniorCounselManifest) -> str:
    password = os.environ.get(INITIAL_PASSWORD_ENV, "")
    if not password:
        raise InitializationRefused(f"{INITIAL_PASSWORD_ENV} must be set.")
    if len(password) < auth.MIN_PASSWORD_LENGTH:
        raise InitializationRefused(
            f"{INITIAL_PASSWORD_ENV} must be at least {auth.MIN_PASSWORD_LENGTH} characters."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise InitializationRefused(
            f"{INITIAL_PASSWORD_ENV} must be at most {MAX_PASSWORD_LENGTH} characters."
        )
    if password == DEFAULT_SEED_PASSWORD:
        raise InitializationRefused(f"{INITIAL_PASSWORD_ENV} must not use the demo password.")
    if password.casefold() in {admin.username.casefold(), admin.name.casefold()}:
        raise InitializationRefused(
            f"{INITIAL_PASSWORD_ENV} must not equal the administrator name or username."
        )
    if "\x00" in password:
        raise InitializationRefused(f"{INITIAL_PASSWORD_ENV} contains an invalid character.")
    return password


def _application_tables() -> list[str]:
    return sorted(table.name for table in m.Base.metadata.sorted_tables)


def _quoted_public_table(connection: Connection, table: str) -> str:
    preparer = connection.dialect.identifier_preparer
    return f"{preparer.quote_schema('public')}.{preparer.quote(table)}"


def _nonempty_tables(session: Session, tables: Sequence[str]) -> list[str]:
    nonempty: list[str] = []
    connection = session.connection()
    for table in tables:
        qualified = _quoted_public_table(connection, table)
        if session.execute(text(f"SELECT 1 FROM {qualified} LIMIT 1")).first():
            nonempty.append(table)
    return nonempty


def _lock_application_tables(session: Session, tables: Sequence[str]) -> None:
    if not tables:
        raise InitializationRefused("Migrated schema contains no application tables.")
    connection = session.connection()
    qualified = ", ".join(_quoted_public_table(connection, table) for table in tables)
    session.execute(text(f"LOCK TABLE {qualified} IN ACCESS EXCLUSIVE MODE"))


def _pre_migration_nonempty_tables(connection: Connection) -> list[str]:
    """Detect a prior initialization before Alembic can alter its schema."""
    tables = sorted(
        table
        for table in inspect(connection).get_table_names(schema="public")
        if table != "alembic_version"
    )
    nonempty: list[str] = []
    for table in tables:
        qualified = _quoted_public_table(connection, table)
        if connection.execute(text(f"SELECT 1 FROM {qualified} LIMIT 1")).first():
            nonempty.append(table)
    return nonempty


def _assert_owner_identity(connection: Connection, owner_url: str) -> str:
    expected_owner = make_url(owner_url).username
    if not expected_owner:
        raise InitializationRefused("Migration-owner database URL has no username.")
    current_user = connection.execute(text("SELECT current_user")).scalar_one()
    if current_user != expected_owner:
        raise InitializationRefused(
            "Migration-owner connection authenticated as an unexpected database role."
        )
    return current_user


def _assert_migration_owner(session: Session, owner_url: str, tables: Sequence[str]) -> None:
    connection = session.connection()
    current_user = _assert_owner_identity(connection, owner_url)
    owners = dict(
        session.execute(
            text("SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'public'")
        ).all()
    )
    missing = [table for table in tables if table not in owners]
    wrong_owner = [table for table in tables if owners.get(table) != current_user]
    if missing:
        raise InitializationRefused("Migration did not create the complete application schema.")
    if wrong_owner:
        raise InitializationRefused(
            "Migration connection does not own every application table."
        )


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _create_initial_records(
    session: Session,
    manifest: ProductionInitializationManifest,
    password: str,
) -> None:
    matter = manifest.matter
    admin = manifest.initial_senior_counsel
    now = datetime.now(UTC)

    case = m.CaseFile(
        id=matter.id,
        name=matter.name,
        aircraft=matter.aircraft,
        accident_date=matter.accident_date,
        location=matter.location,
        matter_type=matter.matter_type,
        status=matter.status,
        docket_ref=matter.docket_ref,
    )
    reviewer = m.Reviewer(
        id=admin.id,
        name=admin.name,
        role=SENIOR_COUNSEL_ROLE,
        username=admin.username,
        password_hash=auth.hash_password(password),
        must_change_password=True,
        password_changed_at=None,
        mfa_secret=None,
        mfa_enabled_at=None,
        is_active=True,
    )
    session.add_all([case, reviewer])
    session.flush()

    session.add(
        m.CaseMember(
            id=_new_id("cm"),
            case_id=case.id,
            reviewer_id=reviewer.id,
            role=SENIOR_COUNSEL_ROLE,
            is_active=True,
        )
    )
    session.flush()

    account_event = m.AccountAuditEvent(
        id=_new_id("aae"),
        at=now,
        actor_reviewer_id=reviewer.id,
        actor=reviewer.name,
        actor_role=SENIOR_COUNSEL_ROLE,
        action="reviewer provisioned",
        subject_reviewer_id=reviewer.id,
        previous_status=None,
        new_status="active",
        detail=(
            f"Initial Senior Aviation Counsel {reviewer.name} provisioned as "
            f"{reviewer.username}; password rotation and MFA enrollment required."
        ),
        previous_integrity_hash=None,
        integrity_hash="",
    )
    account_event.integrity_hash = chain_sha256(
        previous_hash=None,
        content=account_audit_event_content(account_event),
    )

    case_event = m.AuditEvent(
        id=_new_id("ae"),
        case_id=case.id,
        at=now,
        actor_reviewer_id=reviewer.id,
        actor=reviewer.name,
        actor_role=SENIOR_COUNSEL_ROLE,
        action="matter initialized",
        subject_type="reviewer",
        subject_id=reviewer.id,
        previous_status=None,
        new_status="active",
        detail=(
            f"Matter {case.name} initialized with {reviewer.name} as active "
            "Senior Aviation Counsel."
        ),
        report_eligibility_changed=False,
        previous_integrity_hash=None,
        integrity_hash="",
    )
    case_event.integrity_hash = chain_sha256(
        previous_hash=None,
        content=audit_event_content(case_event),
    )
    session.add_all([account_event, case_event])
    session.flush()


def _initialize_locked(
    engine: Engine,
    *,
    owner_url: str,
    runtime_url: str,
    manifest: ProductionInitializationManifest,
    password: str,
) -> None:
    tables = _application_tables()

    # Refuse an already initialized database before changing grants.  The
    # session-level initializer lock prevents another initializer racing this
    # check; the final transaction also locks and rechecks every app table.
    with Session(engine) as session, session.begin():
        _assert_migration_owner(session, owner_url, tables)
        if nonempty := _nonempty_tables(session, tables):
            raise InitializationRefused(
                "Production database is not empty; populated tables: " + ", ".join(nonempty)
            )

    grant_runtime_privileges(owner_url, runtime_url)

    with Session(engine) as session, session.begin():
        _lock_application_tables(session, tables)
        _assert_migration_owner(session, owner_url, tables)
        if nonempty := _nonempty_tables(session, tables):
            raise InitializationRefused(
                "Production database became nonempty during initialization; "
                "no initializer records were created. Populated tables: " + ", ".join(nonempty)
            )
        _create_initial_records(session, manifest, password)


def initialize_production(manifest_path: str | Path) -> InitializationResult:
    if not is_production():
        raise InitializationRefused(
            "Production initialization requires ATLAS_ARGUS_ENV=production."
        )
    assert_production_config(require_migration_url=True)

    manifest = load_manifest(Path(manifest_path))
    password = _initial_password(manifest.initial_senior_counsel)
    owner_url = migration_database_url()
    runtime_url = runtime_database_url()
    engine = create_engine(owner_url, pool_pre_ping=True)
    try:
        # A session lock spans Alembic's separate connection and both data
        # transactions, serializing concurrent one-shot attempts.
        with engine.connect() as lock_connection:
            lock_connection.execute(
                text("SELECT pg_advisory_lock(hashtext(:lock_key))"),
                {"lock_key": INITIALIZER_LOCK_KEY},
            )
            try:
                _assert_owner_identity(lock_connection, owner_url)
                if nonempty := _pre_migration_nonempty_tables(lock_connection):
                    raise InitializationRefused(
                        "Production database is not empty; populated tables: "
                        + ", ".join(nonempty)
                    )
                # The advisory lock is session-scoped and survives this commit.
                # Release preflight's relation-level read locks before the final
                # transaction requests ACCESS EXCLUSIVE locks on those tables.
                lock_connection.commit()
                upgrade_to_head()
                _initialize_locked(
                    engine,
                    owner_url=owner_url,
                    runtime_url=runtime_url,
                    manifest=manifest,
                    password=password,
                )
            finally:
                lock_connection.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:lock_key))"),
                    {"lock_key": INITIALIZER_LOCK_KEY},
                )
    finally:
        engine.dispose()

    admin = manifest.initial_senior_counsel
    return InitializationResult(
        matter_id=manifest.matter.id,
        reviewer_id=admin.id,
        username=admin.username,
    )


def _safe_error_message(exc: BaseException) -> str:
    message = str(exc)
    password = os.environ.get(INITIAL_PASSWORD_ENV, "")
    if password:
        message = message.replace(password, "[REDACTED]")
    return message


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Initialize one empty Atlas Argus production database."
    )
    parser.add_argument(
        "manifest",
        type=Path,
        help="Path to the credential-free JSON manifest (matter metadata may be confidential)",
    )
    args = parser.parse_args(argv)
    try:
        result = initialize_production(args.manifest)
    except Exception as exc:  # CLI boundary: concise error, no traceback/secrets.
        print(f"Production initialization refused: {_safe_error_message(exc)}", file=sys.stderr)
        return 1
    print(
        "Production initialization complete for matter "
        f"{result.matter_id}; initial administrator {result.username}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
