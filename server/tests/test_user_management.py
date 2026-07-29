"""Password rotation and deactivation doctrine."""

from __future__ import annotations

from conftest import login_as
from sqlalchemy import text

from atlas_argus.db.seed import reset_and_seed
from atlas_argus.db.session import SessionLocal

NEW_PASSWORD = "a-much-better-password"


def _change(client, current="argus-demo", new=NEW_PASSWORD):
    return client.post(
        "/api/auth/change-password",
        json={"currentPassword": current, "newPassword": new},
    )


def test_change_password_rotates_and_keeps_current_session(client):
    login_as(client)
    assert _change(client).status_code == 200
    # The current session keeps working…
    assert client.get("/api/case").status_code == 200
    # …the old password no longer signs in, the new one does.
    old = client.post("/api/auth/login", json={"username": "mokafor", "password": "argus-demo"})
    assert old.status_code == 401
    new = client.post("/api/auth/login", json={"username": "mokafor", "password": NEW_PASSWORD})
    assert new.status_code == 200
    assert new.json()["mustChangePassword"] is False


def test_change_password_revokes_other_sessions(client):
    from fastapi.testclient import TestClient

    from atlas_argus.api.app import app

    login_as(client)
    with TestClient(app) as other:
        login_as(other)
        assert other.get("/api/case").status_code == 200
        # Rotating the password on the first session kills the second.
        assert _change(client).status_code == 200
        assert other.get("/api/case").status_code == 401
    assert client.get("/api/case").status_code == 200


def test_change_password_validation(client):
    login_as(client)
    wrong_current = _change(client, current="not-my-password")
    assert wrong_current.status_code == 401
    assert "Current password is incorrect" in wrong_current.json()["detail"]
    too_short = _change(client, new="short")
    assert too_short.status_code == 422
    same = _change(client, new="argus-demo")
    assert same.status_code == 422
    assert client.post("/api/auth/change-password", json={}).status_code == 422
    # Nothing above rotated anything: the original password still works.
    login_as(client)


def test_must_change_password_flag_surfaces_and_clears(client):
    with SessionLocal() as s, s.begin():
        s.execute(
            text("UPDATE reviewer SET must_change_password = true WHERE id = 'rev-okafor'")
        )
    response = client.post(
        "/api/auth/login", json={"username": "mokafor", "password": "argus-demo"}
    )
    assert response.json()["mustChangePassword"] is True
    assert client.get("/api/auth/session").json()["mustChangePassword"] is True
    assert _change(client).status_code == 200
    assert client.get("/api/auth/session").json()["mustChangePassword"] is False


def test_deactivated_reviewer_is_locked_out_uniformly(client):
    login_as(client)
    assert client.get("/api/case").status_code == 200
    with SessionLocal() as s, s.begin():
        s.execute(text("UPDATE reviewer SET is_active = false WHERE id = 'rev-okafor'"))
    # Existing session dies immediately…
    assert client.get("/api/case").status_code == 401
    # …and sign-in fails with the uniform message (no account oracle).
    response = client.post(
        "/api/auth/login", json={"username": "mokafor", "password": "argus-demo"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password."


def test_admin_can_provision_reviewers(client):
    login_as(client)
    response = client.post(
        "/api/admin/reviewers",
        json={
            "name": "Avery Chen",
            "username": "achen",
            "role": "Claims Reviewer",
            "initialPassword": "temporary-strong-password",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    reviewer = body["reviewer"]
    assert reviewer == {
        "id": reviewer["id"],
        "name": "Avery Chen",
        "username": "achen",
        "role": "Claims Reviewer",
        "isActive": True,
        "mfaEnabled": False,
        "mustChangePassword": True,
        "passwordChangedAt": None,
    }
    assert body["accountAuditEvent"]["action"] == "reviewer provisioned"
    assert body["accountAuditEvent"]["subjectReviewerId"] == reviewer["id"]

    sign_in = client.post(
        "/api/auth/login",
        json={"username": "achen", "password": "temporary-strong-password"},
    )
    assert sign_in.status_code == 200
    assert sign_in.json()["mustChangePassword"] is True


def test_admin_reviewer_listing_is_counsel_only(client):
    login_as(client, "pnatarajan")
    forbidden = client.get("/api/admin/reviewers")
    assert forbidden.status_code == 403

    client.post("/api/auth/logout")
    login_as(client)
    response = client.get("/api/admin/reviewers")
    assert response.status_code == 200
    reviewers = response.json()["reviewers"]
    assert {r["username"] for r in reviewers} >= {
        "mokafor",
        "dreeves",
        "pnatarajan",
        "tlindqvist",
    }
    assert all("passwordHash" not in r for r in reviewers)
    by_username = {reviewer["username"]: reviewer for reviewer in reviewers}
    assert by_username["mokafor"]["mfaEnabled"] is True
    assert by_username["pnatarajan"]["mfaEnabled"] is True
    assert by_username["tlindqvist"]["mfaEnabled"] is False


def test_admin_deactivation_revokes_existing_sessions(client):
    from fastapi.testclient import TestClient

    from atlas_argus.api.app import app

    login_as(client)
    with TestClient(app) as target:
        login_as(target, "pnatarajan")
        assert target.get("/api/case").status_code == 200

        response = client.post("/api/admin/reviewers/rev-natarajan/deactivate")
        assert response.status_code == 200, response.text
        assert response.json()["reviewer"]["isActive"] is False
        assert response.json()["accountAuditEvent"]["newStatus"] == "inactive"
        assert target.get("/api/case").status_code == 401

    blocked = client.post(
        "/api/auth/login", json={"username": "pnatarajan", "password": "argus-demo"}
    )
    assert blocked.status_code == 401

    reactivated = client.post("/api/admin/reviewers/rev-natarajan/reactivate")
    assert reactivated.status_code == 200
    assert reactivated.json()["reviewer"]["isActive"] is True
    login_as(client, "pnatarajan")


def test_admin_cannot_deactivate_self(client):
    login_as(client)
    response = client.post("/api/admin/reviewers/rev-okafor/deactivate")
    assert response.status_code == 422
    assert "cannot deactivate your own account" in response.json()["detail"]


def test_admin_password_reset_forces_rotation_and_revokes_sessions(client):
    from fastapi.testclient import TestClient

    from atlas_argus.api.app import app

    login_as(client)
    with TestClient(app) as target:
        login_as(target, "pnatarajan")
        assert target.get("/api/case").status_code == 200

        response = client.post(
            "/api/admin/reviewers/rev-natarajan/reset-password",
            json={"newPassword": "replacement-password"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["reviewer"]["mustChangePassword"] is True
        assert response.json()["accountAuditEvent"]["action"] == "reviewer password reset"
        assert target.get("/api/case").status_code == 401

    old = client.post(
        "/api/auth/login", json={"username": "pnatarajan", "password": "argus-demo"}
    )
    assert old.status_code == 401
    new = client.post(
        "/api/auth/login",
        json={"username": "pnatarajan", "password": "replacement-password"},
    )
    assert new.status_code == 200
    assert new.json()["mustChangePassword"] is True


def test_mfa_reset_requires_authenticated_mfa_verified_senior_counsel(client):
    from atlas_argus import auth

    endpoint = "/api/admin/reviewers/rev-natarajan/reset-mfa"
    assert client.post(endpoint).status_code == 401

    password_only = client.post(
        "/api/auth/login", json={"username": "mokafor", "password": "argus-demo"}
    )
    assert password_only.status_code == 200
    not_verified = client.post(endpoint)
    assert not_verified.status_code == 403
    assert not_verified.json()["detail"] == auth.MFA_ENROLLMENT_REQUIRED

    assert client.post("/api/auth/logout").status_code == 200
    login_as(client, "pnatarajan")
    non_senior = client.post("/api/admin/reviewers/rev-lindqvist/reset-mfa")
    assert non_senior.status_code == 403
    assert "Senior Aviation Counsel" in non_senior.json()["detail"]


def test_admin_cannot_reset_own_mfa(client):
    from sqlalchemy import select

    from atlas_argus.db.models import Reviewer

    login_as(client)
    response = client.post("/api/admin/reviewers/rev-okafor/reset-mfa")
    assert response.status_code == 422
    assert "own MFA factor" in response.json()["detail"]

    with SessionLocal() as session:
        reviewer = session.execute(
            select(Reviewer).where(Reviewer.id == "rev-okafor")
        ).scalar_one()
        assert reviewer.mfa_secret is not None
        assert reviewer.mfa_enabled_at is not None


def test_admin_cannot_reset_reviewer_without_configured_mfa(client):
    login_as(client)
    response = client.post("/api/admin/reviewers/rev-natarajan/reset-mfa")
    assert response.status_code == 422
    assert response.json()["detail"] == "Reviewer does not have MFA configured."


def test_admin_mfa_reset_revokes_sessions_clears_throttle_and_appends_audit(client):
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from atlas_argus import auth
    from atlas_argus.api.app import app
    from atlas_argus.db.models import AccountAuditEvent, AuthSession, LoginThrottle, Reviewer

    login_as(client)
    with TestClient(app) as target:
        login_as(target, "pnatarajan")
        with SessionLocal() as session:
            reviewer = session.get(Reviewer, "rev-natarajan")
            assert reviewer is not None
            assert reviewer.mfa_secret is not None
            valid_code = auth._totp_at(reviewer.mfa_secret, int(auth.time.time()))
        invalid_code = "000000" if valid_code != "000000" else "000001"
        assert (
            target.post("/api/auth/mfa/verify", json={"code": invalid_code}).status_code == 422
        )

        response = client.post("/api/admin/reviewers/rev-natarajan/reset-mfa")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reviewer"]["mfaEnabled"] is False
        assert body["accountAuditEvent"]["action"] == "reviewer MFA reset"
        assert body["accountAuditEvent"]["subjectReviewerId"] == "rev-natarajan"
        assert body["accountAuditEvent"]["previousStatus"] == "enabled"
        assert body["accountAuditEvent"]["newStatus"] == "not_enrolled"
        assert target.get("/api/auth/session").status_code == 401

    throttle_key = auth._mfa_throttle_key("rev-natarajan")
    with SessionLocal() as session:
        reviewer = session.get(Reviewer, "rev-natarajan")
        assert reviewer is not None
        assert reviewer.mfa_secret is None
        assert reviewer.mfa_enabled_at is None
        assert (
            session.execute(
                select(AuthSession).where(
                    AuthSession.reviewer_id == reviewer.id,
                    AuthSession.revoked_at.is_(None),
                )
            ).first()
            is None
        )
        assert (
            session.execute(
                select(LoginThrottle).where(LoginThrottle.username == throttle_key)
            ).first()
            is None
        )
        events = list(
            session.execute(
                select(AccountAuditEvent).where(
                    AccountAuditEvent.action == "reviewer MFA reset",
                    AccountAuditEvent.subject_reviewer_id == reviewer.id,
                )
            ).scalars()
        )
        assert len(events) == 1

    assert client.get("/api/admin/account-audit/verify").json()["ok"] is True
    repeated = client.post("/api/admin/reviewers/rev-natarajan/reset-mfa")
    assert repeated.status_code == 422
    assert repeated.json()["detail"] == "Reviewer does not have MFA configured."
    with SessionLocal() as session:
        repeated_events = list(
            session.execute(
                select(AccountAuditEvent).where(
                    AccountAuditEvent.action == "reviewer MFA reset",
                    AccountAuditEvent.subject_reviewer_id == "rev-natarajan",
                )
            ).scalars()
        )
        assert len(repeated_events) == 1


def test_account_audit_survives_case_reset(client):
    login_as(client)
    created = client.post(
        "/api/admin/reviewers",
        json={
            "name": "Avery Chen",
            "username": "achen",
            "role": "Claims Reviewer",
            "initialPassword": "temporary-strong-password",
        },
    )
    assert created.status_code == 201
    event_id = created.json()["accountAuditEvent"]["id"]
    with SessionLocal() as s, s.begin():
        reset_and_seed(s)

    audit = client.get("/api/admin/account-audit")
    assert audit.status_code == 200
    assert any(event["id"] == event_id for event in audit.json()["events"])


def test_account_audit_is_append_only(client):
    login_as(client)
    created = client.post(
        "/api/admin/reviewers",
        json={
            "name": "Avery Chen",
            "username": "achen",
            "role": "Claims Reviewer",
            "initialPassword": "temporary-strong-password",
        },
    )
    event_id = created.json()["accountAuditEvent"]["id"]
    with SessionLocal() as s, s.begin():
        try:
            s.execute(
                text("UPDATE account_audit_event SET detail = 'tampered' WHERE id = :id"),
                {"id": event_id},
            )
        except Exception as exc:
            assert "account_audit_event is append-only" in str(exc)
        else:
            raise AssertionError("account audit mutation was not rejected")
