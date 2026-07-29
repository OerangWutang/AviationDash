"""Programmatic Alembic entry points — no alembic.ini; the migration scripts
live inside the package so an installed distribution can migrate itself."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

from .guards import INGESTION_APPEND_ONLY_TABLES

#: Evidentiary tables the runtime role may insert into but never rewrite. This
#: is a second, grant-level layer beneath the append-only triggers: a table
#: missing here still has its trigger, but silently keeps a privilege it
#: should not have. ``test_append_only_grants`` asserts this list stays
#: complete, because nothing else would notice an omission.
APPEND_ONLY_TABLES = (
    "audit_event",
    "account_audit_event",
    "review_decision",
    "report_section_revision",
    "packet_artifact",
    *INGESTION_APPEND_ONLY_TABLES,
)


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    return config


def upgrade_to_head() -> None:
    command.upgrade(alembic_config(), "head")


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def grant_runtime_privileges(migration_database_url: str, runtime_database_url: str) -> None:
    """Grant the runtime database role DML without ownership/DDL privileges.

    The migration role owns schema objects. The runtime role can use the app
    tables and sequences, but append-only evidentiary tables are insert/select
    only. This is still not WORM storage, but it removes schema-owner powers
    from the running API role.
    """
    migration_role = make_url(migration_database_url).username
    runtime_role = make_url(runtime_database_url).username
    if not migration_role:
        raise RuntimeError("Migration database URL must include a username.")
    if not runtime_role:
        raise RuntimeError("Runtime database URL must include a username.")
    if runtime_role == migration_role:
        raise RuntimeError(
            "Runtime database role must be distinct from the migration-owner role."
        )
    role = _quote_identifier(runtime_role)
    engine = create_engine(migration_database_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            role_state = conn.execute(
                text(
                    "SELECT rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = :runtime_role"
                ),
                {"runtime_role": runtime_role},
            ).one_or_none()
            if role_state is None:
                raise RuntimeError(
                    f"Runtime database role {runtime_role!r} does not exist."
                )
            if role_state.rolsuper:
                raise RuntimeError(
                    f"Runtime database role {runtime_role!r} must not be SUPERUSER."
                )
            if role_state.rolbypassrls:
                raise RuntimeError(
                    f"Runtime database role {runtime_role!r} must not have BYPASSRLS."
                )
            owned_tables = list(
                conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tableowner = :runtime_role "
                        "ORDER BY tablename"
                    ),
                    {"runtime_role": runtime_role},
                ).scalars()
            )
            if owned_tables:
                raise RuntimeError(
                    f"Runtime database role {runtime_role!r} must not own public "
                    f"application tables: {', '.join(owned_tables)}."
                )
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE "
                    f"ON ALL TABLES IN SCHEMA public TO {role}"
                )
            )
            conn.execute(
                text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
            )
            conn.execute(
                text(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {role}")
            )
            conn.execute(
                text(
                    f"REVOKE UPDATE, DELETE ON "
                    f"{', '.join(APPEND_ONLY_TABLES)} FROM {role}"
                )
            )
    finally:
        engine.dispose()


def drop_everything(engine: Engine) -> None:
    """Dev/test only: drop all application schema objects and Alembic's
    version bookkeeping so the next upgrade rebuilds from the baseline.

    Use schema reset instead of ``Base.metadata.drop_all()``: model metadata can
    be ahead of an old test database, and drop_all then tries to remove
    constraints that do not exist yet.
    """
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
