"""Create append-only external integrity checkpoints for one or all matters."""

from __future__ import annotations

import argparse

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ..config import migration_database_url
from ..integrity_anchors import write_anchor
from ..services import compute_case_integrity
from . import models as m


def create_anchors(case_ids: list[str]) -> list[str]:
    engine = create_engine(migration_database_url(), pool_pre_ping=True)
    created: list[str] = []
    try:
        with Session(engine) as session, session.begin():
            selected_ids = case_ids or list(
                session.execute(select(m.CaseFile.id).order_by(m.CaseFile.id)).scalars()
            )
            for case_id in selected_ids:
                if session.get(m.CaseFile, case_id) is None:
                    raise RuntimeError(f"Matter not found: {case_id}")
                verification = compute_case_integrity(
                    session,
                    case_id,
                    interactive=False,
                )
                if not verification["ok"]:
                    raise RuntimeError(
                        f"Refusing to anchor matter {case_id}: internal integrity failed."
                    )
                created.append(str(write_anchor(case_id, verification)))
    finally:
        engine.dispose()
    return created


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id", nargs="*", help="Matter ids; omit to anchor all matters")
    args = parser.parse_args()
    for path in create_anchors(args.case_id):
        print(path)


if __name__ == "__main__":
    main()
