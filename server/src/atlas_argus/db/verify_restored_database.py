"""Fail-closed verification for a database restored during a recovery drill.

This deliberately runs with the migration-owner connection: a runtime-role
connection is subject to matter RLS and could report a clean database while
silently omitting rows it cannot see.
"""

from __future__ import annotations

from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from ..config import migration_database_url
from ..services import compute_case_integrity
from . import models as m
from .migrate import alembic_config


def verify_restored_database() -> dict[str, int]:
    engine = create_engine(migration_database_url(), pool_pre_ping=True)
    try:
        expected_heads = set(ScriptDirectory.from_config(alembic_config()).get_heads())
        with Session(engine) as session, session.begin():
            restored_heads = set(
                session.execute(text("SELECT version_num FROM alembic_version")).scalars()
            )
            if restored_heads != expected_heads:
                raise RuntimeError(
                    "Restored schema revision does not match this release: "
                    f"expected {sorted(expected_heads)}, found {sorted(restored_heads)}."
                )

            # Selecting every mapped entity catches incomplete/older schemas,
            # including a missing column on an otherwise present table.
            for mapper in m.Base.registry.mappers:
                session.execute(select(mapper.local_table).limit(1)).first()

            counts = {
                "matters": session.scalar(select(func.count(m.CaseFile.id))) or 0,
                "reviewers": session.scalar(select(func.count(m.Reviewer.id))) or 0,
                "audit_events": session.scalar(select(func.count(m.AuditEvent.id))) or 0,
            }
            empty = [label for label, count in counts.items() if count == 0]
            if empty:
                raise RuntimeError(
                    "Restored database is missing required operational data: "
                    + ", ".join(empty)
                    + "."
                )

            case_ids = list(
                session.execute(select(m.CaseFile.id).order_by(m.CaseFile.id)).scalars()
            )
            for case_id in case_ids:
                verification = compute_case_integrity(
                    session,
                    case_id,
                    interactive=False,
                )
                if not verification["ok"]:
                    broken = [
                        name
                        for name in (
                            "audit",
                            "packets",
                            "sections",
                            "decisions",
                            "extractions",
                        )
                        if not verification[name]["ok"]
                    ]
                    raise RuntimeError(
                        f"Integrity verification failed for matter {case_id}: "
                        + ", ".join(broken)
                        + "."
                    )
            counts["verified_matters"] = len(case_ids)
            return counts
    finally:
        engine.dispose()


def main() -> None:
    counts = verify_restored_database()
    print(
        "restore verification ok: "
        f"{counts['verified_matters']} matters, "
        f"{counts['reviewers']} reviewers, "
        f"{counts['audit_events']} audit events"
    )


if __name__ == "__main__":
    main()
