from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import time
from base64 import b64encode
from collections import defaultdict
from hashlib import sha256
from http.cookies import SimpleCookie
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from .. import auth
from ..config import (
    assert_production_config,
    is_production,
    max_request_body_bytes,
    max_source_request_bytes,
    metrics_token,
    request_body_timeout_seconds,
)
from ..db import models as m
from ..db.session import SessionLocal, set_rls_reviewer_context
from ..domain.types import DomainError
from ..packet import PACKET_CSS
from .admission import (  # noqa: F401 - compatibility re-exports for callers/tests
    ingestion_admission,
    packet_render_admission,
    source_body_admission,
)
from .routes import router

DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
logger = logging.getLogger("atlas_argus.api")
# Uvicorn's default configuration does not assign a level to this application
# logger, so it can inherit WARNING and silently drop the structured request
# records. Keep propagation enabled and make the intended INFO contract explicit.
logger.setLevel(logging.INFO)
request_counts: dict[tuple[str, str, int], int] = defaultdict(int)
request_duration_seconds: dict[tuple[str, str], float] = defaultdict(float)
PACKET_STYLE_CSP_HASH = "sha256-" + b64encode(
    sha256(PACKET_CSS.encode("utf-8")).digest()
).decode("ascii")
_SOURCE_UPLOAD_PATH = re.compile(r"^/api/cases/([^/]+)/sources/?$")


def _cookie_token(headers: dict[str, str]) -> str | None:
    raw = headers.get("cookie", "")
    if not raw:
        return None
    cookies = SimpleCookie()
    try:
        cookies.load(raw)
    except Exception:  # malformed Cookie headers are unauthenticated
        return None
    morsel = cookies.get(auth.SESSION_COOKIE)
    return morsel.value if morsel is not None else None


def _source_upload_auth_failure(
    token: str | None, case_id: str
) -> tuple[int, str] | None:
    """Authenticate and step-up before accepting a multi-megabyte body.

    This runs in a worker thread from the ASGI middleware so the synchronous
    SQLAlchemy lookup cannot block the event loop while a client is uploading.
    The route re-authenticates in each write phase; this is only the early
    resource-admission boundary.
    """
    with SessionLocal() as session:
        reviewer = auth.resolve_session(session, token)
        if reviewer is None:
            return 401, "Not signed in."
        if reviewer.must_change_password:
            return 403, "Change your password before accessing case evidence."
        if not auth.mfa_enabled(reviewer):
            return 403, auth.MFA_ENROLLMENT_REQUIRED
        if not auth.mfa_verified_for_session(session, token):
            return 403, auth.MFA_REQUIRED
        set_rls_reviewer_context(session, reviewer.id)
        membership = session.execute(
            select(m.CaseMember.id).where(
                m.CaseMember.case_id == case_id,
                m.CaseMember.reviewer_id == reviewer.id,
                m.CaseMember.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if membership is None:
            return 403, "You do not have active access to this matter."
    return None


class BodySizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        source_upload_match = _SOURCE_UPLOAD_PATH.fullmatch(path)
        is_source_upload = source_upload_match is not None
        limit = max_source_request_bytes() if is_source_upload else max_request_body_bytes()
        if not is_source_upload:
            await self._call_with_limit(scope, receive, send, limit=limit)
            return

        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        failure = await asyncio.to_thread(
            _source_upload_auth_failure,
            _cookie_token(headers),
            source_upload_match.group(1),
        )
        if failure is not None:
            status, detail = failure
            await JSONResponse(status_code=status, content={"detail": detail})(
                scope, receive, send
            )
            return
        if not source_body_admission.try_acquire():
            await JSONResponse(
                status_code=503,
                content={"detail": "Too many document uploads are in progress."},
            )(scope, receive, send)
            return
        try:
            await self._call_with_limit(scope, receive, send, limit=limit)
        finally:
            source_body_admission.release()

    async def _call_with_limit(self, scope, receive, send, *, limit: int):
        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > limit:
                    await JSONResponse(
                        status_code=413,
                        content={"detail": "Request body too large."},
                    )(scope, receive, send)
                    return
            except ValueError:
                await JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header."},
                )(scope, receive, send)
                return

        chunks: list[bytes] = []
        received = 0
        more_body = True
        deadline = time.monotonic() + request_body_timeout_seconds()
        while more_body:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await JSONResponse(
                    status_code=408,
                    content={"detail": "Request body was not received before the deadline."},
                )(scope, receive, send)
                return
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except TimeoutError:
                await JSONResponse(
                    status_code=408,
                    content={"detail": "Request body was not received before the deadline."},
                )(scope, receive, send)
                return
            if message["type"] != "http.request":
                await self.app(scope, receive, send)
                return
            body = message.get("body", b"")
            received += len(body)
            if received > limit:
                await JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large."},
                )(scope, receive, send)
                return
            chunks.append(body)
            more_body = message.get("more_body", False)

        body = b"".join(chunks)
        sent = False

        async def replay_receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_receive, send)


def cors_origins() -> list[str]:
    # Packaged production is same-origin. If the variable is omitted, fail to
    # the safer empty allow-list instead of silently enabling localhost origins.
    default = "" if is_production() else DEFAULT_CORS_ORIGINS
    raw = os.environ.get("ATLAS_ARGUS_CORS_ORIGINS", default)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app() -> FastAPI:
    # Fail closed: in ATLAS_ARGUS_ENV=production, refuse to boot on dev
    # defaults rather than run quietly misconfigured.
    assert_production_config()
    app = FastAPI(
        title="Atlas Argus API",
        description=(
            "Evidence-review backend for aviation accident litigation. "
            "Mirrors the frontend domain contracts; see docs/api-contract.md."
        ),
        version="0.1.0",
    )

    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_observer(request: Request, call_next):
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed = time.perf_counter() - start
            path = request.url.path
            # Group metrics by the matched ROUTE TEMPLATE (e.g.
            # "/api/report-sections/{section_id}"), not the raw path: an
            # unauthenticated caller can hit arbitrarily many distinct raw
            # paths, and keying these unbounded in-memory dicts by raw path
            # is a memory-exhaustion vector. Unmatched requests (404s, path
            # probes) collapse into one bucket instead of growing forever.
            matched_route = request.scope.get("route")
            route = getattr(matched_route, "path", None) or "unmatched"
            method = request.method
            request_counts[(method, route, status_code)] += 1
            request_duration_seconds[(method, route)] += elapsed
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "method": method,
                        "path": path,
                        "status": status_code,
                        "duration_ms": round(elapsed * 1000, 2),
                    },
                    separators=(",", ":"),
                )
            )

    # Cross-site request forgery backstop behind SameSite=Lax cookies: browsers
    # send an Origin header on state-changing requests — reject any origin that
    # is neither the request's own origin (packaged same-origin deployment) nor
    # in the dev allowlist. Requests without Origin (curl, tests) pass through.
    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            own_origin = f"{request.url.scheme}://{request.url.netloc}"
            if origin is not None and origin != own_origin and origin not in cors_origins():
                return JSONResponse(status_code=403, content={"detail": "Origin not allowed."})
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(_request: Request, call_next):
        response = await call_next(_request)
        response.headers.setdefault("x-content-type-options", "nosniff")
        response.headers.setdefault("x-frame-options", "DENY")
        response.headers.setdefault("referrer-policy", "no-referrer")
        response.headers.setdefault(
            "content-security-policy",
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
            "object-src 'none'; frame-src 'self' blob:; form-action 'self'; "
            f"style-src 'self' '{PACKET_STYLE_CSP_HASH}'",
        )
        if _request.url.path.startswith("/api/"):
            response.headers.setdefault("cache-control", "no-store")
        response.headers.setdefault(
            "permissions-policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    app.include_router(router)

    @app.get("/api/metrics", response_class=PlainTextResponse)
    def metrics(request: Request) -> Response:
        token = metrics_token()
        if is_production() and not token:
            return PlainTextResponse("metrics are not configured\n", status_code=503)
        if is_production() or token:
            authorization = request.headers.get("authorization", "")
            bearer = authorization.removeprefix("Bearer ").strip()
            header_token = request.headers.get("x-metrics-token", "")
            token_matches = bool(token) and (
                hmac.compare_digest(bearer, token) or hmac.compare_digest(header_token, token)
            )
            if not token_matches:
                return PlainTextResponse("metrics authentication required\n", status_code=401)
        lines = [
            "# HELP atlas_argus_http_requests_total HTTP requests by method, path, and status.",
            "# TYPE atlas_argus_http_requests_total counter",
        ]
        for (method, path, status), count in sorted(request_counts.items()):
            lines.append(
                f'atlas_argus_http_requests_total{{method="{method}",path="{path}",status="{status}"}} {count}'
            )
        lines.extend(
            [
                "# HELP atlas_argus_http_request_duration_seconds_sum Total request duration by method and path.",
                "# TYPE atlas_argus_http_request_duration_seconds_sum counter",
            ]
        )
        for (method, path), total in sorted(request_duration_seconds.items()):
            lines.append(
                f'atlas_argus_http_request_duration_seconds_sum{{method="{method}",path="{path}"}} {total:.6f}'
            )
        return PlainTextResponse("\n".join(lines) + "\n")

    # Packaged deployment: serve the built frontend (vite `dist/`, built with
    # VITE_API_URL=/) from the same origin — cookies flow naturally and CORS
    # becomes dev-only. API routes above take precedence over the mount.
    web_dist = os.environ.get("ATLAS_ARGUS_WEB_DIST", "")
    if web_dist and Path(web_dist).is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")

    return app


app = create_app()
