"""Session authentication.

- Argon2id password hashing (argon2-cffi defaults).
- Bearer token in an HttpOnly cookie; only its SHA-256 lives in the database,
  so a database leak does not leak live sessions.
- Sessions are server-side and revocable; expiry is checked on every resolve.
- Login failures are uniform ("Invalid username or password.") — no username
  oracle.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .db import models as m
from .domain.types import PermissionDenied, RateLimited, Unauthorized, ValidationFailure

MIN_PASSWORD_LENGTH = 10

SESSION_COOKIE = "atlas_session"
SESSION_TTL = timedelta(hours=12)
LOGIN_FAILED = "Invalid username or password."

# Brute-force throttle: consecutive failures per normalized username lock the
# account out briefly. State is stored in Postgres so it is shared by all API
# processes.
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 60.0
LOGIN_LOCKED = "Too many failed sign-in attempts — try again shortly."
LOGIN_THROTTLE_PREFIX = "login:"
GLOBAL_LOGIN_THROTTLE_KEY = "login-global"
GLOBAL_LOGIN_MAX_ATTEMPTS = 30
GLOBAL_LOGIN_WINDOW = timedelta(seconds=60)
GLOBAL_LOGIN_LOCKOUT_SECONDS = 60.0
GLOBAL_LOGIN_LOCKED = "Too many sign-in attempts — try again shortly."
MFA_REQUIRED = "MFA verification required for this action."
MFA_ENROLLMENT_REQUIRED = "MFA enrollment required for this action."
MFA_ALREADY_ENABLED = (
    "MFA is already enabled. Use the approved account-recovery workflow to replace it."
)
MFA_INVALID = "Invalid MFA code."
MFA_MAX_FAILED_ATTEMPTS = 5
MFA_LOCKOUT_SECONDS = 60.0
MFA_LOCKED = "Too many invalid MFA codes — try again shortly."
MFA_THROTTLE_PREFIX = "mfa:"
MFA_ISSUER = "Atlas Argus"
MFA_PERIOD_SECONDS = 30
MFA_DIGITS = 6
MFA_WINDOW = 1

# A throttle row is written for every attempted username, including ones that
# don't exist — otherwise an unknown username would skip the throttle and
# leak a timing/enumeration signal. Left unswept, an unauthenticated caller
# could grow this table without bound by attempting logins with an unlimited
# number of distinct usernames. Rows that are no longer locked and have been
# quiet for a while are reclaimed lazily, the same way dead sessions are.
STALE_THROTTLE_WINDOW = timedelta(hours=1)


def _now() -> datetime:
    return datetime.now(UTC)


def reset_login_throttle(session: Session | None = None) -> None:
    if session is not None:
        session.execute(delete(m.LoginThrottle))


_hasher = PasswordHasher()
# Fixed hash to verify against for unknown usernames, so a login attempt
# against a username that doesn't exist takes roughly the same time as one
# against a real username with the wrong password — the error message is
# already uniform ("Invalid username or password."), but skipping the Argon2
# verify entirely for unknown usernames would still leak a timing signal an
# attacker could use to enumerate valid usernames.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_mfa_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _decode_mfa_secret(secret: str) -> bytes:
    padding = "=" * ((8 - len(secret) % 8) % 8)
    return base64.b32decode((secret + padding).upper())


def _totp_at(secret: str, for_time: int) -> str:
    counter = int(for_time / MFA_PERIOD_SECONDS)
    digest = hmac.new(
        _decode_mfa_secret(secret),
        struct.pack(">Q", counter),
        hashlib.sha1,
    ).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**MFA_DIGITS)).zfill(MFA_DIGITS)


def verify_totp(secret: str, code: str, *, at_time: int | None = None) -> bool:
    clean = code.replace(" ", "").strip()
    if not clean.isdigit() or len(clean) != MFA_DIGITS:
        return False
    now = int(time.time()) if at_time is None else at_time
    for drift in range(-MFA_WINDOW, MFA_WINDOW + 1):
        candidate = _totp_at(secret, now + drift * MFA_PERIOD_SECONDS)
        if hmac.compare_digest(candidate, clean):
            return True
    return False


def mfa_otpauth_uri(reviewer: m.Reviewer, secret: str) -> str:
    label = quote(f"{MFA_ISSUER}:{reviewer.username}")
    issuer = quote(MFA_ISSUER)
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={issuer}"
        f"&algorithm=SHA1&digits={MFA_DIGITS}&period={MFA_PERIOD_SECONDS}"
    )


def purge_dead_sessions(session: Session) -> None:
    """Drop expired and revoked sessions. Dead sessions carry no audit value
    (the audit trail records actions, not sign-ins) — lazy cleanup on login
    keeps the table from growing without bound."""
    session.execute(
        delete(m.AuthSession).where(
            or_(
                m.AuthSession.expires_at < datetime.now(UTC),
                m.AuthSession.revoked_at.is_not(None),
            )
        )
    )


def _normalize_username(username: str) -> str:
    return username.strip().lower()


def _throttle_row(session: Session, key: str) -> m.LoginThrottle:
    # A SELECT-then-INSERT races when two first attempts for the same key
    # arrive together: both can observe no row and one then fails the primary
    # key insert with a 500. PostgreSQL's upsert serializes that first creation;
    # the locking read below then serializes the counter update itself.
    session.execute(
        pg_insert(m.LoginThrottle)
        .values(
            username=key,
            failures=0,
            locked_until=None,
            updated_at=_now(),
        )
        .on_conflict_do_nothing(index_elements=[m.LoginThrottle.username])
    )
    throttle = session.execute(
        select(m.LoginThrottle).where(m.LoginThrottle.username == key).with_for_update()
    ).scalar_one()
    return throttle


def _login_throttle_key(username: str) -> str:
    # Login requests accept arbitrary strings so their namespace must be
    # explicit; otherwise an attempted username such as ``mfa:<reviewer-id>``
    # could collide with, and deliberately lock, that reviewer's MFA counter.
    return f"{LOGIN_THROTTLE_PREFIX}{username}"


def _record_login_failure(session: Session, username: str) -> None:
    now = _now()
    throttle = _throttle_row(session, _login_throttle_key(username))
    failures = throttle.failures + 1
    throttle.failures = failures
    throttle.locked_until = (
        now + timedelta(seconds=LOCKOUT_SECONDS) if failures >= MAX_FAILED_LOGINS else None
    )
    throttle.updated_at = now
    session.flush()


def _consume_global_login_admission(session: Session) -> None:
    """Bound aggregate login work across usernames and API processes.

    The fixed row lock also serializes Argon2 verification, preventing a burst
    of random usernames from running many expensive hashes concurrently.
    ``updated_at`` is the start of the current fixed window, not the latest
    attempt, so a low steady rate does not eventually lock everyone out.
    """
    now = _now()
    throttle = _throttle_row(session, GLOBAL_LOGIN_THROTTLE_KEY)
    if throttle.locked_until is not None:
        if now < throttle.locked_until:
            raise RateLimited(GLOBAL_LOGIN_LOCKED)
        throttle.failures = 0
        throttle.locked_until = None
        throttle.updated_at = now

    if now - throttle.updated_at >= GLOBAL_LOGIN_WINDOW:
        throttle.failures = 0
        throttle.updated_at = now

    throttle.failures += 1
    if throttle.failures > GLOBAL_LOGIN_MAX_ATTEMPTS:
        throttle.locked_until = now + timedelta(seconds=GLOBAL_LOGIN_LOCKOUT_SECONDS)
        session.flush()
        raise RateLimited(GLOBAL_LOGIN_LOCKED)
    session.flush()


def _clear_throttle(session: Session, key: str) -> None:
    session.execute(delete(m.LoginThrottle).where(m.LoginThrottle.username == key))


def _clear_login_failures(session: Session, username: str) -> None:
    _clear_throttle(session, _login_throttle_key(username))


def clear_mfa_throttle(session: Session, reviewer_id: str) -> None:
    """Clear factor-verification failures for one reviewer."""
    _clear_throttle(session, _mfa_throttle_key(reviewer_id))


def _sweep_stale_login_throttle(session: Session) -> None:
    cutoff = _now() - STALE_THROTTLE_WINDOW
    session.execute(
        delete(m.LoginThrottle).where(
            m.LoginThrottle.updated_at < cutoff,
            or_(
                m.LoginThrottle.locked_until.is_(None),
                m.LoginThrottle.locked_until < _now(),
            ),
        )
    )


def login(session: Session, username: str, password: str) -> tuple[m.Reviewer, str]:
    """Verify credentials and mint a session; returns (reviewer, bearer token)."""
    purge_dead_sessions(session)
    _sweep_stale_login_throttle(session)
    username = _normalize_username(username)
    now = _now()

    _consume_global_login_admission(session)
    throttle = _throttle_row(session, _login_throttle_key(username))
    if throttle.locked_until is not None and now < throttle.locked_until:
        raise RateLimited(LOGIN_LOCKED)

    reviewer = session.execute(
        select(m.Reviewer).where(m.Reviewer.username == username)
    ).scalar_one_or_none()
    password_hash = reviewer.password_hash if reviewer is not None else _DUMMY_HASH
    password_ok = _verify_password(password_hash, password)
    if (
        reviewer is None
        or not password_ok
        # Uniform failure for deactivated accounts — no account oracle.
        or not reviewer.is_active
    ):
        _record_login_failure(session, username)
        raise Unauthorized(LOGIN_FAILED)
    _clear_login_failures(session, username)

    token = secrets.token_urlsafe(32)
    session.add(
        m.AuthSession(
            token_hash=_token_hash(token),
            reviewer_id=reviewer.id,
            created_at=now,
            expires_at=now + SESSION_TTL,
            mfa_verified_at=None,
            revoked_at=None,
        )
    )
    session.flush()
    return reviewer, token


def resolve_session(session: Session, token: str | None) -> m.Reviewer | None:
    if not token:
        return None
    auth = session.get(m.AuthSession, _token_hash(token))
    if auth is None or auth.revoked_at is not None:
        return None
    if auth.expires_at < _now():
        return None
    reviewer = session.get(m.Reviewer, auth.reviewer_id)
    if reviewer is None or not reviewer.is_active:
        return None
    return reviewer


def resolve_auth_session(session: Session, token: str | None) -> m.AuthSession | None:
    if not token:
        return None
    auth = session.get(m.AuthSession, _token_hash(token))
    if auth is None or auth.revoked_at is not None:
        return None
    if auth.expires_at < _now():
        return None
    return auth


def mfa_enabled(reviewer: m.Reviewer) -> bool:
    return reviewer.mfa_secret is not None and reviewer.mfa_enabled_at is not None


def mfa_verified_for_session(session: Session, token: str | None) -> bool:
    auth_session = resolve_auth_session(session, token)
    return auth_session is not None and auth_session.mfa_verified_at is not None


def require_mfa(session: Session, reviewer: m.Reviewer, token: str | None) -> None:
    if not mfa_enabled(reviewer):
        raise PermissionDenied(MFA_ENROLLMENT_REQUIRED)
    if not mfa_verified_for_session(session, token):
        raise PermissionDenied(MFA_REQUIRED)


def mfa_status(session: Session, reviewer: m.Reviewer, token: str | None) -> dict:
    return {
        "enabled": mfa_enabled(reviewer),
        "verified": mfa_verified_for_session(session, token),
    }


def _mfa_throttle_key(reviewer_id: str) -> str:
    # Login and MFA keys have explicit namespaces so arbitrary login input can
    # never collide with a reviewer's factor-verification counter.
    return f"{MFA_THROTTLE_PREFIX}{reviewer_id}"


def _verify_mfa_attempt(
    session: Session,
    reviewer: m.Reviewer,
    secret: str,
    code: str,
) -> None:
    """Verify one factor code under a reviewer-scoped, cross-process lockout.

    The row lock serializes concurrent API workers. Callers must commit
    ``ValidationFailure`` and ``RateLimited`` side effects instead of relying
    on the normal request transaction, which rolls back domain errors.
    """
    now = _now()
    key = _mfa_throttle_key(reviewer.id)
    throttle = _throttle_row(session, key)
    if throttle.locked_until is not None:
        if now < throttle.locked_until:
            raise RateLimited(MFA_LOCKED)
        # The lockout elapsed: the next attempt starts a fresh window.
        throttle.failures = 0
        throttle.locked_until = None
        throttle.updated_at = now

    if verify_totp(secret, code):
        clear_mfa_throttle(session, reviewer.id)
        session.flush()
        return

    throttle.failures += 1
    throttle.updated_at = now
    if throttle.failures >= MFA_MAX_FAILED_ATTEMPTS:
        throttle.locked_until = now + timedelta(seconds=MFA_LOCKOUT_SECONDS)
        session.flush()
        raise RateLimited(MFA_LOCKED)
    session.flush()
    raise ValidationFailure(MFA_INVALID)


def start_mfa_enrollment(reviewer: m.Reviewer) -> dict:
    # Never replace an established factor from a password-authenticated session.
    # Login deliberately creates a session before MFA verification, so allowing
    # this endpoint to rotate an enabled secret would let a password-only attacker
    # enroll their own factor and then mark that same session MFA-verified.
    if mfa_enabled(reviewer):
        raise PermissionDenied(MFA_ALREADY_ENABLED)
    if reviewer.mfa_secret is None:
        reviewer.mfa_secret = generate_mfa_secret()
    return {
        "secret": reviewer.mfa_secret,
        "otpauthUri": mfa_otpauth_uri(reviewer, reviewer.mfa_secret),
    }


def enable_mfa(session: Session, reviewer: m.Reviewer, token: str | None, code: str) -> None:
    if reviewer.mfa_secret is None:
        raise ValidationFailure("Start MFA enrollment before verifying a code.")
    _verify_mfa_attempt(session, reviewer, reviewer.mfa_secret, code)
    reviewer.mfa_enabled_at = _now()
    mark_mfa_verified(session, token)
    session.flush()


def mark_mfa_verified(session: Session, token: str | None) -> None:
    auth_session = resolve_auth_session(session, token)
    if auth_session is None:
        raise Unauthorized("Not signed in.")
    auth_session.mfa_verified_at = _now()
    session.flush()


def verify_mfa_code(
    session: Session, reviewer: m.Reviewer, token: str | None, code: str
) -> None:
    if not mfa_enabled(reviewer):
        raise PermissionDenied(MFA_ENROLLMENT_REQUIRED)
    _verify_mfa_attempt(session, reviewer, reviewer.mfa_secret or "", code)
    mark_mfa_verified(session, token)


def validate_new_password(new_password: str, current_password: str) -> str | None:
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return f"New password must be at least {MIN_PASSWORD_LENGTH} characters."
    if new_password == current_password:
        return "New password must differ from the current password."
    return None


def change_password(
    session: Session,
    reviewer: m.Reviewer,
    current_token: str | None,
    current_password: str,
    new_password: str,
) -> None:
    """Rotate the reviewer's password and revoke every OTHER session — a
    stolen session does not survive the owner rotating their password."""
    if not _verify_password(reviewer.password_hash, current_password):
        raise Unauthorized("Current password is incorrect.")
    error = validate_new_password(new_password, current_password)
    if error:
        raise ValidationFailure(error)
    reviewer.password_hash = hash_password(new_password)
    reviewer.must_change_password = False
    reviewer.password_changed_at = datetime.now(UTC)
    stmt = (
        update(m.AuthSession)
        .where(
            m.AuthSession.reviewer_id == reviewer.id,
            m.AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    if current_token:
        stmt = stmt.where(m.AuthSession.token_hash != _token_hash(current_token))
    session.execute(stmt)
    session.flush()


def revoke_session(session: Session, token: str | None) -> None:
    if not token:
        return
    auth = session.get(m.AuthSession, _token_hash(token))
    if auth is not None and auth.revoked_at is None:
        auth.revoked_at = datetime.now(UTC)
        session.flush()
