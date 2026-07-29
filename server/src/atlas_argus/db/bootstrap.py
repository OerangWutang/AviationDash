"""Migrate the schema to head and seed the sample case in development.

    python -m atlas_argus.db.bootstrap [--reset]

Idempotent: Alembic brings the schema to head (no-op when current) and an
already-initialized database is left alone. Production refuses an empty
database until the one-shot greenfield initializer has run. ``--reset`` is a
development-only rebuild: drops everything (sessions included) and reseeds.
"""

from __future__ import annotations

import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ..config import (
    assert_production_config,
    is_production,
    migration_database_url,
    runtime_database_url,
)
from .migrate import drop_everything, grant_runtime_privileges, upgrade_to_head
from .seed import is_seeded, reset_and_seed, seed_database
from .session import SessionLocal, engine


def _database_is_seeded(database_url: str) -> bool:
    """Check deployment readiness without runtime RLS hiding every case.

    Production calls this with the migration-owner URL. The runtime role has
    no reviewer context during bootstrap, so its intentionally fail-closed RLS
    view is empty even when real matters already exist.
    """
    readiness_engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with Session(readiness_engine) as session, session.begin():
            return is_seeded(session)
    finally:
        readiness_engine.dispose()


def bootstrap(reset: bool = False) -> str:
    assert_production_config(require_migration_url=True)
    if is_production() and reset:
        raise RuntimeError("Refusing to reset or seed sample data in production.")
    if reset:
        drop_everything(engine)
    upgrade_to_head()
    if is_production():
        owner_url = migration_database_url()
        grant_runtime_privileges(owner_url, runtime_database_url())
        if not _database_is_seeded(owner_url):
            raise RuntimeError(
                "Production database is empty; initialize a real matter and "
                "reviewer directory through an approved deployment/import "
                "process, not the sample-case seed."
            )
        return "schema migrated to head; already seeded"
    with SessionLocal() as session:
        with session.begin():
            if reset:
                reset_and_seed(session)
                return "schema rebuilt from migrations; database reset and seeded"
            if not is_seeded(session):
                seed_database(session)
                return "schema migrated to head; database seeded"
            return "schema migrated to head; already seeded (use --reset to reseed)"


if __name__ == "__main__":
    print(bootstrap(reset="--reset" in sys.argv[1:]))
