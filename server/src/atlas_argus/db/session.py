"""Engine and session factory.

``ATLAS_ARGUS_DATABASE_URL`` is the runtime API role. In production this must
be distinct from ``ATLAS_ARGUS_MIGRATION_DATABASE_URL`` so the running API is
not the schema owner.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from ..config import runtime_database_url

DATABASE_URL = runtime_database_url()

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def set_rls_reviewer_context(session: Session, reviewer_id: str) -> None:
    """Set the transaction-local identity used by Postgres RLS policies."""
    session.execute(
        text("SELECT set_config('atlas_argus.reviewer_id', :reviewer_id, true)"),
        {"reviewer_id": reviewer_id},
    )


@contextmanager
def short_request_session() -> Iterator[Session]:
    """One transaction, opened and closed around a bounded unit of work.

    This is the single place a request-serving transaction is started. Routes
    that cannot hold a connection for their whole duration — document
    ingestion spends minutes in OCR with no database work to do — use this
    directly for each of their short phases instead of constructing a bare
    ``SessionLocal()``, so they cannot drift from the connection and
    transaction handling every other route gets.

    Reviewer identity and the RLS context are established by the auth
    dependency that runs inside this transaction (``require_reviewer`` calls
    ``set_rls_reviewer_context``), so a caller must resolve a reviewer per
    session rather than reusing one across sessions.
    """
    with SessionLocal() as session:
        with session.begin():
            yield session


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one transaction per request — commit on success,
    roll back on any exception (domain errors included)."""
    with short_request_session() as session:
        yield session


def get_unmanaged_session() -> Iterator[Session]:
    """FastAPI dependency for endpoints that need to commit domain-error side
    effects, such as failed-login throttle counters."""
    with SessionLocal() as session:
        yield session
