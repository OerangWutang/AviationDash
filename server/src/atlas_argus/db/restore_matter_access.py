"""Break-glass recovery for a matter nobody can reach any more.

Matter administration is deliberately confined to the matter: every membership
route requires the acting reviewer's own active membership, so a global account
administrator cannot quietly add themselves to a matter they were walled off
from. The cost of that wall is a reachable dead end. A matter is *stranded*
when it has no active membership backed by an active reviewer account — most
plausibly when one administrator removes a colleague's membership and that
administrator's own account is later deactivated on offboarding. From then on
no API caller can list, add, or reactivate members, and row-level security
makes the matter invisible to the runtime role entirely:

    case_file rows visible  : 0
    case_member rows visible: 0
    INSERT INTO case_member : blocked by row-level security policy

That is why recovery is an operator command against the migration-owner
connection rather than an API endpoint. Granting the runtime role a privileged
function that writes ``case_member`` rows would hand exactly the same power to
anyone holding the runtime credential — the threat row-level security is
documented as defence in depth against — and would let them strand a matter
first in order to claim it. The owner credential is the correct trust boundary
for an act that steps outside the application's own authorization plane.

Run only with the migration-owner credentials::

    python -m atlas_argus.db.restore_matter_access --case-id case-1 \\
        --username mokafor

The restore refuses unless the matter is genuinely stranded, so it can never
substitute for ordinary membership administration.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from ..config import assert_production_config, is_production, migration_database_url
from ..integrity import audit_event_content, chain_sha256
from . import models as m
from .initialize_production import _assert_migration_owner, _application_tables

RESTORE_LOCK_KEY = "atlas_argus.matter_access_restore.v1"
SENIOR_COUNSEL_ROLE = "Senior Aviation Counsel"


class RestoreRefused(RuntimeError):
    """The restore stopped without changing any matter access."""


@dataclass(frozen=True)
class RestoreResult:
    case_id: str
    case_name: str
    reviewer_id: str
    username: str
    created_membership: bool


def _effective_members(session: Session, case_id: str) -> list[str]:
    """Senior administrators who can restore ordinary access right now.

    A membership only confers authority if the account behind it can still
    sign in, so both flags have to hold.
    """
    return list(
        session.execute(
            select(m.CaseMember.reviewer_id)
            .join(m.Reviewer, m.Reviewer.id == m.CaseMember.reviewer_id)
            .where(
                m.CaseMember.case_id == case_id,
                m.CaseMember.is_active.is_(True),
                m.CaseMember.role == SENIOR_COUNSEL_ROLE,
                m.Reviewer.is_active.is_(True),
                m.Reviewer.role == SENIOR_COUNSEL_ROLE,
            )
            .order_by(m.CaseMember.reviewer_id)
        ).scalars()
    )


def _latest_audit_hash(session: Session, case_id: str) -> str | None:
    # Mirrors services._latest_audit_hash. The chain link itself is computed by
    # the shared integrity helpers, so only this ordering query is restated.
    return session.execute(
        select(m.AuditEvent.integrity_hash)
        .where(m.AuditEvent.case_id == case_id)
        .order_by(m.AuditEvent.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def _lock_audit_chain(session: Session, case_id: str) -> None:
    # Identical lock name and function to services._lock_audit_chain, so this
    # command and a concurrent API append serialize against each other.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
        {"lock_name": f"atlas_argus:audit_event:{case_id}"},
    )


def _append_restore_audit(
    session: Session,
    *,
    case: m.CaseFile,
    reviewer: m.Reviewer,
    operator: str,
    created_membership: bool,
) -> m.AuditEvent:
    """Record the restore on the matter's own append-only chain.

    The actor is the database operator, not the reviewer whose access was
    restored: attributing an out-of-band act to a reviewer who did not perform
    it would corrupt exactly the attribution this audit trail exists to prove.
    ``audit_event.actor_reviewer_id`` is nullable for this reason. The account
    chain requires a non-null reviewer actor and is deliberately left alone.
    """
    _lock_audit_chain(session, case.id)
    previous_hash = _latest_audit_hash(session, case.id)
    verb = "granted" if created_membership else "reactivated"
    event = m.AuditEvent(
        id=f"ae-{uuid4().hex}",
        case_id=case.id,
        at=datetime.now(UTC),
        actor_reviewer_id=None,
        actor=f"database operator ({operator})",
        actor_role="Break-glass operator",
        action="matter access restored",
        subject_type="reviewer",
        subject_id=reviewer.id,
        previous_status="stranded",
        new_status="active",
        detail=(
            f"Matter had no active membership backed by an active account. "
            f"Out-of-band restore run with migration-owner credentials by "
            f"database role {operator}: {reviewer.name} ({reviewer.username}) "
            f"{verb} an active {SENIOR_COUNSEL_ROLE} membership."
        ),
        report_eligibility_changed=False,
        previous_integrity_hash=previous_hash,
        integrity_hash="",
    )
    event.integrity_hash = chain_sha256(
        previous_hash=previous_hash,
        content=audit_event_content(event),
    )
    session.add(event)
    session.flush()
    return event


def _restore_locked(session: Session, *, case_id: str, username: str, owner_url: str) -> RestoreResult:
    operator = _assert_migration_owner_identity(session, owner_url)

    case = session.execute(
        select(m.CaseFile).where(m.CaseFile.id == case_id).with_for_update()
    ).scalar_one_or_none()
    if case is None:
        raise RestoreRefused(f"Matter not found: {case_id}.")

    reviewer = session.execute(
        select(m.Reviewer).where(m.Reviewer.username == username.strip().lower())
    ).scalar_one_or_none()
    if reviewer is None:
        raise RestoreRefused(f"Reviewer not found: {username}.")
    if not reviewer.is_active:
        raise RestoreRefused(
            f"Reviewer {reviewer.username} is deactivated; reactivate the account first."
        )
    if reviewer.role != SENIOR_COUNSEL_ROLE:
        # Whoever regains the matter has to be able to re-admit everyone else,
        # which is a Senior Aviation Counsel act on both planes.
        raise RestoreRefused(
            f"Reviewer {reviewer.username} holds the global role {reviewer.role!r}; "
            f"matter access is restored only to a {SENIOR_COUNSEL_ROLE}."
        )

    if reachable := _effective_members(session, case.id):
        raise RestoreRefused(
            f"Matter {case.id} is not stranded — it still has an active Senior for: "
            + ", ".join(reachable)
            + ". Use the membership administration API."
        )

    existing = session.execute(
        select(m.CaseMember)
        .where(m.CaseMember.case_id == case.id, m.CaseMember.reviewer_id == reviewer.id)
        .with_for_update()
    ).scalar_one_or_none()
    created_membership = existing is None
    if existing is None:
        session.add(
            m.CaseMember(
                id=f"cm-{uuid4().hex}",
                case_id=case.id,
                reviewer_id=reviewer.id,
                role=SENIOR_COUNSEL_ROLE,
                is_active=True,
            )
        )
    else:
        existing.role = SENIOR_COUNSEL_ROLE
        existing.is_active = True
    session.flush()

    _append_restore_audit(
        session,
        case=case,
        reviewer=reviewer,
        operator=operator,
        created_membership=created_membership,
    )
    return RestoreResult(
        case_id=case.id,
        case_name=case.name,
        reviewer_id=reviewer.id,
        username=reviewer.username,
        created_membership=created_membership,
    )


def _assert_migration_owner_identity(session: Session, owner_url: str) -> str:
    """Fail loudly if this is not really the schema owner.

    A runtime-role connection would silently see an empty matter through
    row-level security and could "prove" any matter stranded.
    """
    _assert_migration_owner(session, owner_url, _application_tables())
    return session.execute(text("SELECT current_user")).scalar_one()


def restore_matter_access(*, case_id: str, username: str) -> RestoreResult:
    if is_production():
        assert_production_config(require_migration_url=True)
    owner_url = migration_database_url()
    engine = create_engine(owner_url, pool_pre_ping=True)
    try:
        with Session(engine) as session, session.begin():
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": RESTORE_LOCK_KEY},
            )
            return _restore_locked(
                session, case_id=case_id, username=username, owner_url=owner_url
            )
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Restore Senior Aviation Counsel access to a stranded matter. "
            "Requires the migration-owner database credentials."
        )
    )
    parser.add_argument("--case-id", required=True, help="Matter identifier to recover")
    parser.add_argument(
        "--username",
        required=True,
        help="Username of the active Senior Aviation Counsel to readmit",
    )
    args = parser.parse_args(argv)
    try:
        result = restore_matter_access(case_id=args.case_id, username=args.username)
    except Exception as exc:  # CLI boundary: concise error, no traceback.
        print(f"Matter access restore refused: {exc}", file=sys.stderr)
        return 1
    verb = "granted" if result.created_membership else "reactivated"
    print(
        f"Restored access to {result.case_id} ({result.case_name}): "
        f"{result.username} {verb} an active {SENIOR_COUNSEL_ROLE} membership. "
        "The restore is recorded on the matter audit chain."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
