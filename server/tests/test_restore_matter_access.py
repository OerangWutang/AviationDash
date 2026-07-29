from __future__ import annotations

import pytest
from sqlalchemy import select, text

from atlas_argus.db import models as m
from atlas_argus.db.restore_matter_access import (
    RestoreRefused,
    main,
    restore_matter_access,
)
from atlas_argus.db.session import SessionLocal
from atlas_argus.services import verify_case_integrity
from conftest import login_as

CASE_ID = "case-3407"


def _strand_by_membership() -> None:
    """Every membership inactive — the permanent form of the dead end.

    The membership routes cannot produce this on their own: the acting
    administrator must be an active member and is refused when removing their
    own access, so one active membership always survives an API call. It is
    what a partial data import, a botched bulk edit, or a restore of a backup
    taken mid-change leaves behind, and nothing in the API can undo it.
    """
    with SessionLocal() as session, session.begin():
        session.execute(
            text("UPDATE case_member SET is_active = false WHERE case_id = :case_id"),
            {"case_id": CASE_ID},
        )


def _strand_by_account() -> None:
    """Reachable through ordinary administration.

    Memberships are removed down to one administrator, whose reviewer account
    is then deactivated on offboarding. The memberships still read active, but
    no account behind them can sign in.
    """
    with SessionLocal() as session, session.begin():
        session.execute(
            text(
                "UPDATE case_member SET is_active = false "
                "WHERE case_id = :case_id AND reviewer_id <> 'rev-okafor'"
            ),
            {"case_id": CASE_ID},
        )
        session.execute(
            text("UPDATE reviewer SET is_active = false WHERE id = 'rev-okafor'")
        )


def _membership(reviewer_id: str) -> m.CaseMember | None:
    with SessionLocal() as session:
        return session.execute(
            select(m.CaseMember).where(
                m.CaseMember.case_id == CASE_ID,
                m.CaseMember.reviewer_id == reviewer_id,
            )
        ).scalar_one_or_none()



def test_stranded_matter_is_unreachable_through_the_api(client):
    """The condition the command exists to recover from.

    The reviewer's account is fine — they simply have no way back into the
    matter, including through the administration routes.
    """
    _strand_by_membership()

    login_as(client, "mokafor")
    assert client.get("/api/cases").json()["cases"] == []
    assert client.get(f"/api/cases/{CASE_ID}").status_code == 403
    assert client.get(f"/api/admin/cases/{CASE_ID}/members").status_code == 403
    assert (
        client.post(
            f"/api/admin/cases/{CASE_ID}/members",
            json={"reviewerId": "rev-okafor", "role": "Senior Aviation Counsel"},
        ).status_code
        == 403
    )


def test_restore_readmits_senior_counsel_and_api_works_again(client):
    _strand_by_membership()

    result = restore_matter_access(case_id=CASE_ID, username="mokafor")

    assert result.case_id == CASE_ID
    assert result.username == "mokafor"
    assert result.created_membership is False
    membership = _membership("rev-okafor")
    assert membership is not None
    assert membership.is_active is True
    assert membership.role == "Senior Aviation Counsel"

    login_as(client, "mokafor")
    state = client.get(f"/api/cases/{CASE_ID}")
    assert state.status_code == 200
    assert state.json()["caseMembership"]["role"] == "Senior Aviation Counsel"
    # Only the restored reviewer is readmitted; everyone else stays removed.
    assert _membership("rev-natarajan").is_active is False


def test_restore_recovers_a_matter_stranded_by_account_deactivation():
    """The API-reachable variant: memberships read active but no account can
    sign in. Readmitting a different, active Senior ends the dead end without
    reactivating a departed administrator's credentials."""
    _strand_by_account()
    with SessionLocal() as session, session.begin():
        session.execute(
            text(
                "UPDATE reviewer SET role = 'Senior Aviation Counsel' "
                "WHERE id = 'rev-natarajan'"
            )
        )

    result = restore_matter_access(case_id=CASE_ID, username="pnatarajan")

    assert result.username == "pnatarajan"
    assert _membership("rev-natarajan").is_active is True
    # The offboarded administrator's account stays deactivated.
    with SessionLocal() as session:
        assert session.get(m.Reviewer, "rev-okafor").is_active is False


def test_restore_is_recorded_on_the_matter_audit_chain():
    _strand_by_membership()

    restore_matter_access(case_id=CASE_ID, username="mokafor")

    with SessionLocal() as session:
        event = session.execute(
            select(m.AuditEvent)
            .where(m.AuditEvent.case_id == CASE_ID)
            .order_by(m.AuditEvent.seq.desc())
            .limit(1)
        ).scalar_one()
        # The operator is the actor; the restore is not misattributed to the
        # reviewer who merely regained access.
        assert event.action == "matter access restored"
        assert event.actor_reviewer_id is None
        assert event.actor.startswith("database operator (")
        assert event.subject_id == "rev-okafor"
        assert "migration-owner credentials" in event.detail

        reviewer = session.get(m.Reviewer, "rev-okafor")
        assert reviewer is not None
        verification = verify_case_integrity(session, reviewer, case_id=CASE_ID)

    assert verification["ok"] is True, verification
    assert verification["audit"]["ok"] is True


def test_restore_refuses_a_matter_that_is_still_reachable():
    with pytest.raises(RestoreRefused, match="is not stranded"):
        restore_matter_access(case_id=CASE_ID, username="mokafor")

    membership = _membership("rev-natarajan")
    assert membership is not None and membership.is_active is True


def test_restore_refuses_an_unknown_matter():
    with pytest.raises(RestoreRefused, match="Matter not found"):
        restore_matter_access(case_id="case-does-not-exist", username="mokafor")


def test_restore_refuses_an_unknown_reviewer():
    _strand_by_membership()
    with pytest.raises(RestoreRefused, match="Reviewer not found"):
        restore_matter_access(case_id=CASE_ID, username="nobody")


def test_restore_refuses_a_deactivated_reviewer():
    _strand_by_account()  # leaves rev-okafor's account deactivated
    with pytest.raises(RestoreRefused, match="deactivated"):
        restore_matter_access(case_id=CASE_ID, username="mokafor")


def test_restore_refuses_a_reviewer_who_is_not_global_senior_counsel():
    _strand_by_membership()
    # pnatarajan is a global Claims Reviewer.
    with pytest.raises(RestoreRefused, match="matter access is restored only to a"):
        restore_matter_access(case_id=CASE_ID, username="pnatarajan")

    assert _membership("rev-natarajan").is_active is False


def test_restore_refuses_once_the_matter_is_reachable_again():
    """Recovering one matter must not leave a standing backdoor."""
    _strand_by_membership()
    restore_matter_access(case_id=CASE_ID, username="mokafor")

    with pytest.raises(RestoreRefused, match="is not stranded"):
        restore_matter_access(case_id=CASE_ID, username="mokafor")


def test_cli_reports_refusal_without_a_traceback(capsys):
    exit_code = main(["--case-id", CASE_ID, "--username", "mokafor"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Matter access restore refused:" in captured.err
    assert "is not stranded" in captured.err
    assert captured.out == ""


def test_cli_restores_and_reports_success(capsys):
    _strand_by_membership()

    exit_code = main(["--case-id", CASE_ID, "--username", "mokafor"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"Restored access to {CASE_ID}" in captured.out
    assert "recorded on the matter audit chain" in captured.out
    assert _membership("rev-okafor").is_active is True
