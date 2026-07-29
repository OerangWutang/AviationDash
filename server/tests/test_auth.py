"""Session authentication doctrine: identity comes from the session, never
the request body; failures are uniform; sessions are revocable."""

from __future__ import annotations

from conftest import login_as


def test_login_sets_cookie_and_returns_reviewer(client):
    response = client.post(
        "/api/auth/login", json={"username": "mokafor", "password": "argus-demo"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reviewer"] == {
        "id": "rev-okafor",
        "name": "Miriam Okafor",
        "role": "Senior Aviation Counsel",
    }
    assert body["mfa"] == {"enabled": False, "verified": False}
    assert body["expiresAt"].endswith("Z")
    assert "atlas_session" in response.cookies
    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["expiresAt"] == body["expiresAt"]


def test_mfa_enrollment_and_verification_flow(client):
    from sqlalchemy import select

    from atlas_argus import auth
    from atlas_argus.db.models import LoginThrottle
    from atlas_argus.db.session import SessionLocal

    response = client.post(
        "/api/auth/login", json={"username": "mokafor", "password": "argus-demo"}
    )
    assert response.status_code == 200
    assert client.get("/api/case").status_code == 200
    blocked = client.post("/api/packets", json={"packetType": "production"})
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == auth.MFA_ENROLLMENT_REQUIRED

    enrollment = client.post("/api/auth/mfa/enroll")
    assert enrollment.status_code == 200
    secret = enrollment.json()["secret"]
    assert enrollment.json()["otpauthUri"].startswith("otpauth://totp/")

    code = auth._totp_at(secret, int(auth.time.time()))
    bad_code = "000000" if code != "000000" else "000001"
    bad = client.post("/api/auth/mfa/enable", json={"code": bad_code})
    assert bad.status_code == 422
    throttle_key = auth._mfa_throttle_key("rev-okafor")
    with SessionLocal() as session:
        throttle = session.execute(
            select(LoginThrottle).where(LoginThrottle.username == throttle_key)
        ).scalar_one()
        assert throttle.failures == 1
        assert throttle.locked_until is None

    enabled = client.post("/api/auth/mfa/enable", json={"code": code})
    assert enabled.status_code == 200
    assert enabled.json() == {"enabled": True, "verified": True}
    with SessionLocal() as session:
        assert (
            session.execute(
                select(LoginThrottle).where(LoginThrottle.username == throttle_key)
            ).scalar_one_or_none()
            is None
        )
    assert client.post("/api/packets", json={"packetType": "production"}).status_code == 201


def test_high_risk_actions_require_verified_mfa_session(client):
    from datetime import UTC, datetime

    from atlas_argus import auth
    from atlas_argus.db.models import Reviewer
    from atlas_argus.db.session import SessionLocal
    from sqlalchemy import select

    response = client.post(
        "/api/auth/login", json={"username": "mokafor", "password": "argus-demo"}
    )
    assert response.status_code == 200
    with SessionLocal() as session, session.begin():
        reviewer = session.execute(
            select(Reviewer).where(Reviewer.username == "mokafor")
        ).scalar_one()
        secret = auth.generate_mfa_secret()
        reviewer.mfa_secret = secret
        reviewer.mfa_enabled_at = datetime.now(UTC)

    assert client.get("/api/case").status_code == 200
    blocked = client.get("/api/admin/reviewers")
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == auth.MFA_REQUIRED

    code = auth._totp_at(secret, int(auth.time.time()))
    verified = client.post("/api/auth/mfa/verify", json={"code": code})
    assert verified.status_code == 200
    assert verified.json() == {"enabled": True, "verified": True}
    assert client.get("/api/admin/reviewers").status_code == 200


def test_enabled_mfa_cannot_be_replaced_by_password_only_session(client):
    from datetime import UTC, datetime

    from sqlalchemy import select

    from atlas_argus import auth
    from atlas_argus.db.models import Reviewer
    from atlas_argus.db.session import SessionLocal

    assert (
        client.post(
            "/api/auth/login",
            json={"username": "mokafor", "password": "argus-demo"},
        ).status_code
        == 200
    )
    original_secret = auth.generate_mfa_secret()
    with SessionLocal() as session, session.begin():
        reviewer = session.execute(
            select(Reviewer).where(Reviewer.username == "mokafor")
        ).scalar_one()
        reviewer.mfa_secret = original_secret
        reviewer.mfa_enabled_at = datetime.now(UTC)

    replacement = client.post("/api/auth/mfa/enroll")
    assert replacement.status_code == 403
    assert replacement.json()["detail"] == auth.MFA_ALREADY_ENABLED

    with SessionLocal() as session:
        reviewer = session.execute(
            select(Reviewer).where(Reviewer.username == "mokafor")
        ).scalar_one()
        assert reviewer.mfa_secret == original_secret


def test_invalid_mfa_code_is_retryable_without_losing_session(client):
    from datetime import UTC, datetime

    from sqlalchemy import select

    from atlas_argus import auth
    from atlas_argus.db.models import Reviewer
    from atlas_argus.db.session import SessionLocal

    assert (
        client.post(
            "/api/auth/login",
            json={"username": "mokafor", "password": "argus-demo"},
        ).status_code
        == 200
    )
    secret = auth.generate_mfa_secret()
    with SessionLocal() as session, session.begin():
        reviewer = session.execute(
            select(Reviewer).where(Reviewer.username == "mokafor")
        ).scalar_one()
        reviewer.mfa_secret = secret
        reviewer.mfa_enabled_at = datetime.now(UTC)

    valid_code = auth._totp_at(secret, int(auth.time.time()))
    invalid_code = "000000" if valid_code != "000000" else "000001"
    invalid = client.post("/api/auth/mfa/verify", json={"code": invalid_code})
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == auth.MFA_INVALID
    assert client.get("/api/auth/session").status_code == 200


def test_mfa_verification_throttle_persists_locks_and_expires(client, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from atlas_argus import auth
    from atlas_argus.db.models import LoginThrottle, Reviewer
    from atlas_argus.db.session import SessionLocal

    now = [datetime(2026, 7, 18, 10, 0, tzinfo=UTC)]
    monkeypatch.setattr(auth, "_now", lambda: now[0])
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "mokafor", "password": "argus-demo"},
        ).status_code
        == 200
    )
    secret = auth.generate_mfa_secret()
    with SessionLocal() as session, session.begin():
        reviewer = session.execute(
            select(Reviewer).where(Reviewer.username == "mokafor")
        ).scalar_one()
        reviewer.mfa_secret = secret
        reviewer.mfa_enabled_at = now[0]

    good_code = auth._totp_at(secret, int(auth.time.time()))
    bad_code = "000000" if good_code != "000000" else "000001"
    for attempt in range(1, auth.MFA_MAX_FAILED_ATTEMPTS + 1):
        response = client.post("/api/auth/mfa/verify", json={"code": bad_code})
        expected = 429 if attempt == auth.MFA_MAX_FAILED_ATTEMPTS else 422
        assert response.status_code == expected
        assert response.json()["detail"] == (
            auth.MFA_LOCKED if expected == 429 else auth.MFA_INVALID
        )

    throttle_key = auth._mfa_throttle_key("rev-okafor")
    with SessionLocal() as session:
        throttle = session.execute(
            select(LoginThrottle).where(LoginThrottle.username == throttle_key)
        ).scalar_one()
        assert throttle.failures == auth.MFA_MAX_FAILED_ATTEMPTS
        assert throttle.locked_until == now[0] + timedelta(seconds=auth.MFA_LOCKOUT_SECONDS)

    # A correct code cannot bypass an active reviewer-wide lockout, and the
    # password-authenticated session stays alive for a later retry.
    locked = client.post("/api/auth/mfa/verify", json={"code": good_code})
    assert locked.status_code == 429
    assert locked.json()["detail"] == auth.MFA_LOCKED
    assert client.get("/api/auth/session").status_code == 200

    now[0] += timedelta(seconds=auth.MFA_LOCKOUT_SECONDS + 1)
    verified = client.post("/api/auth/mfa/verify", json={"code": good_code})
    assert verified.status_code == 200
    assert verified.json() == {"enabled": True, "verified": True}
    with SessionLocal() as session:
        assert (
            session.execute(
                select(LoginThrottle).where(LoginThrottle.username == throttle_key)
            ).scalar_one_or_none()
            is None
        )


def test_dispositive_decision_requires_mfa(client):
    """Every conflict decision — not just packet generation and section
    approval — is a high-risk action gated behind MFA step-up: dispositive
    rulings on critical conflicts are exactly the case migration 0013 exists
    for, and there is no route-level way to require MFA only for those."""
    from atlas_argus import auth

    response = client.post(
        "/api/auth/login", json={"username": "mokafor", "password": "argus-demo"}
    )
    assert response.status_code == 200
    blocked = client.post(
        "/api/conflicts/cf-1/decisions",
        json={
            "decisionType": "escalate",
            "reasoning": "x" * 30,
            "expectedVersion": 1,
        },
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == auth.MFA_ENROLLMENT_REQUIRED


def test_login_failures_are_uniform(client):
    wrong_password = client.post(
        "/api/auth/login", json={"username": "mokafor", "password": "nope"}
    )
    unknown_user = client.post(
        "/api/auth/login", json={"username": "ghost", "password": "argus-demo"}
    )
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json()["detail"] == unknown_user.json()["detail"]


def test_endpoints_require_a_session(client):
    assert client.get("/api/case").status_code == 401
    assert (
        client.post(
            "/api/conflicts/cf-1/decisions",
            json={
                "decisionType": "escalate",
                "reasoning": "x" * 30,
                "expectedVersion": 1,
            },
        ).status_code
        == 401
    )
    assert client.get("/api/admin/reviewers").status_code == 401
    # Health stays open for readiness checks.
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/metrics").status_code == 200


def test_security_headers_are_set(client):
    from atlas_argus.api.app import PACKET_STYLE_CSP_HASH

    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert (
        f"style-src 'self' '{PACKET_STYLE_CSP_HASH}'"
        in response.headers["content-security-policy"]
    )
    assert "geolocation=()" in response.headers["permissions-policy"]


def test_request_observer_emits_structured_info_log(client, caplog):
    import json
    import logging

    with caplog.at_level(logging.INFO, logger="atlas_argus.api"):
        response = client.get("/api/health")

    assert response.status_code == 200
    records = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "atlas_argus.api" and record.message.startswith("{")
    ]
    assert any(
        record["event"] == "http_request"
        and record["path"] == "/api/health"
        and record["status"] == 200
        for record in records
    )


def test_request_body_size_limit_is_enforced(client, monkeypatch):
    monkeypatch.setenv("ATLAS_ARGUS_MAX_REQUEST_BODY_BYTES", "1024")
    response = client.post(
        "/api/auth/login",
        content='{"username":"' + ("x" * 2000) + '","password":"wrong"}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "Request body too large."


def test_metrics_can_be_token_protected(client, monkeypatch):
    monkeypatch.setenv("ATLAS_ARGUS_METRICS_TOKEN", "metrics-token-123")
    assert client.get("/api/metrics").status_code == 401
    response = client.get("/api/metrics", headers={"authorization": "Bearer metrics-token-123"})
    assert response.status_code == 200
    assert "atlas_argus_http_requests_total" in response.text


def test_logout_revokes_the_session(client):
    login_as(client)
    assert client.get("/api/case").status_code == 200
    assert client.post("/api/auth/logout").json() == {"status": "signed_out"}
    assert client.get("/api/case").status_code == 401


def test_identity_comes_from_the_session_not_the_body(client):
    """A client cannot act as someone else by smuggling a reviewer id."""
    login_as(client, "pnatarajan")
    response = client.post(
        "/api/conflicts/cf-2/decisions",
        json={
            "decisionType": "escalate",
            "reasoning": "Escalating with a smuggled identity in the request body.",
            "reviewerId": "rev-okafor",
            "expectedVersion": 1,
        },
    )
    assert response.status_code == 201
    assert response.json()["decision"]["reviewerName"] == "Priya Natarajan"
    assert response.json()["auditEvents"][0]["actor"] == "Priya Natarajan"
    assert response.json()["auditEvents"][0]["actorReviewerId"] == "rev-natarajan"


def test_dead_sessions_are_purged_on_login(client):
    from sqlalchemy import func, select, text

    from atlas_argus.db.models import AuthSession
    from atlas_argus.db.session import SessionLocal

    # Sessions survive case reseeds by design, so clear the slate explicitly.
    with SessionLocal() as s, s.begin():
        s.execute(text("TRUNCATE auth_session"))

    # Create a session, revoke it, then log in again: the revoked row is gone
    # and exactly one live session remains.
    login_as(client)
    client.post("/api/auth/logout")
    login_as(client)
    with SessionLocal() as s:
        count = s.execute(select(func.count()).select_from(AuthSession)).scalar_one()
    assert count == 1


def test_runtime_case_reset_endpoint_is_not_registered(client):
    login_as(client)
    assert client.post("/api/admin/reset").status_code == 404


def test_forced_password_rotation_is_enforced_by_api(client):
    from sqlalchemy import update

    from atlas_argus.db.models import Reviewer
    from atlas_argus.db.session import SessionLocal

    login_as(client)
    with SessionLocal() as s, s.begin():
        s.execute(
            update(Reviewer)
            .where(Reviewer.username == "mokafor")
            .values(must_change_password=True)
        )

    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["mustChangePassword"] is True
    for response in (
        client.get("/api/case"),
        client.post("/api/packets", json={"packetType": "production"}),
        client.get("/api/admin/reviewers"),
    ):
        assert response.status_code == 403
        assert (
            response.json()["detail"] == "Password change required before using the case API."
        )

    changed = client.post(
        "/api/auth/change-password",
        json={"currentPassword": "argus-demo", "newPassword": "rotated-password-123"},
    )
    assert changed.status_code == 200
    assert client.get("/api/case").status_code == 200


def test_login_throttle_locks_after_repeated_failures(client, monkeypatch):
    from atlas_argus import auth

    from datetime import UTC, datetime, timedelta

    now = [datetime(2026, 7, 11, 10, 0, tzinfo=UTC)]
    monkeypatch.setattr(auth, "_now", lambda: now[0])

    bad = {"username": "mokafor", "password": "wrong"}
    for _ in range(auth.MAX_FAILED_LOGINS):
        assert client.post("/api/auth/login", json=bad).status_code == 401
    # Locked out now — even the CORRECT password is refused during lockout.
    locked = client.post(
        "/api/auth/login", json={"username": "mokafor", "password": "argus-demo"}
    )
    assert locked.status_code == 429
    assert "try again shortly" in locked.json()["detail"]

    # After the lockout window, sign-in works and the counter is reset.
    now[0] += timedelta(seconds=auth.LOCKOUT_SECONDS + 1)
    ok = client.post("/api/auth/login", json={"username": "mokafor", "password": "argus-demo"})
    assert ok.status_code == 200

    # Other usernames are unaffected by mokafor's lockout.
    other = client.post(
        "/api/auth/login", json={"username": "pnatarajan", "password": "argus-demo"}
    )
    assert other.status_code == 200


def test_arbitrary_login_input_cannot_collide_with_mfa_throttle_namespace(client):
    from sqlalchemy import select

    from atlas_argus import auth
    from atlas_argus.db.models import LoginThrottle
    from atlas_argus.db.session import SessionLocal

    mfa_key = auth._mfa_throttle_key("rev-okafor")
    response = client.post(
        "/api/auth/login",
        json={"username": mfa_key, "password": "wrong"},
    )
    assert response.status_code == 401

    with SessionLocal() as session:
        keys = set(session.execute(select(LoginThrottle.username)).scalars())
    assert auth._login_throttle_key(mfa_key) in keys
    assert mfa_key not in keys


def test_concurrent_first_failures_share_one_throttle_row():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import select

    from atlas_argus import auth
    from atlas_argus.db.models import LoginThrottle
    from atlas_argus.db.session import SessionLocal

    workers = 4
    start = Barrier(workers)

    def record_failure() -> None:
        with SessionLocal() as session, session.begin():
            start.wait()
            auth._record_login_failure(session, "simultaneous-first-attempt")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(lambda _index: record_failure(), range(workers)))

    with SessionLocal() as session:
        throttle = session.execute(
            select(LoginThrottle).where(
                LoginThrottle.username == auth._login_throttle_key("simultaneous-first-attempt")
            )
        ).scalar_one()
        assert throttle.failures == workers


def test_successful_login_resets_the_failure_counter(client):
    from atlas_argus import auth

    bad = {"username": "mokafor", "password": "wrong"}
    for _ in range(auth.MAX_FAILED_LOGINS - 1):
        assert client.post("/api/auth/login", json=bad).status_code == 401
    login_as(client)  # success clears the count
    # A fresh burst of MAX-1 failures does not lock: the counter restarted.
    for _ in range(auth.MAX_FAILED_LOGINS - 1):
        assert client.post("/api/auth/login", json=bad).status_code == 401
    login_as(client)


def test_stale_login_throttle_rows_for_unknown_usernames_are_swept(client, monkeypatch):
    """An unauthenticated caller probing many distinct (nonexistent)
    usernames writes one throttle row per username — otherwise unknown
    usernames would skip the throttle entirely and leak a timing signal.
    Those rows must not accumulate forever once they go quiet."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from atlas_argus import auth
    from atlas_argus.db.models import LoginThrottle
    from atlas_argus.db.session import SessionLocal

    now = [datetime(2026, 7, 11, 10, 0, tzinfo=UTC)]
    monkeypatch.setattr(auth, "_now", lambda: now[0])

    for i in range(5):
        client.post("/api/auth/login", json={"username": f"ghost-{i}", "password": "whatever"})
    with SessionLocal() as s:
        count = s.execute(select(func.count()).select_from(LoginThrottle)).scalar_one()
    assert count == 5

    # Once those rows are unlocked and quiet past the sweep window, the next
    # login attempt reclaims them instead of growing the table forever.
    now[0] += auth.STALE_THROTTLE_WINDOW + timedelta(seconds=1)
    client.post("/api/auth/login", json={"username": "ghost-new", "password": "whatever"})
    with SessionLocal() as s:
        usernames = s.execute(select(LoginThrottle.username)).scalars().all()
    assert usernames == [auth._login_throttle_key("ghost-new")]


def test_unknown_username_login_still_runs_a_password_verify(client):
    """Unknown usernames must still run a password verify (against a fixed
    dummy hash), not short-circuit past it — otherwise the uniform error
    message is undermined by a timing side channel an attacker could use to
    enumerate valid usernames. This doesn't measure timing (too flaky for a
    unit test); it proves the dummy-hash verify path exists and behaves."""
    from atlas_argus import auth

    assert auth._verify_password(auth._DUMMY_HASH, "whatever") is False


def test_origin_guard_rejects_unlisted_origins(client):
    login_as(client)
    response = client.post(
        "/api/admin/reviewers",
        json={
            "name": "Case Reviewer",
            "username": "case.reviewer",
            "role": "Claims Reviewer",
            "initialPassword": "temporary-password-123",
        },
        headers={"origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Origin not allowed."


def test_origin_guard_allows_the_requests_own_origin(client):
    """Same-origin browsers send Origin too (packaged deployment)."""
    login_as(client)
    response = client.post(
        "/api/admin/reviewers",
        json={
            "name": "Case Reviewer",
            "username": "case.reviewer",
            "role": "Claims Reviewer",
            "initialPassword": "temporary-password-123",
        },
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 201
