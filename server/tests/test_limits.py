from __future__ import annotations

import asyncio
import importlib

import pytest

from conftest import login_as


def test_claim_text_limit_is_enforced(client):
    login_as(client)
    response = client.post(
        "/api/claims",
        json={
            "sourceDocumentId": "src-fdr",
            "text": "x" * 4001,
            "pageRef": "p. 1",
            "quote": "Quote text",
            "confidence": 0.8,
            "evidenceQuality": "primary",
        },
    )
    assert response.status_code == 422


def test_report_section_citation_count_limit_is_enforced(client):
    login_as(client)
    response = client.post(
        "/api/report-sections",
        json={
            "title": "Citation Count Limit",
            "paragraphRef": "LIM-1",
            "text": "A paragraph long enough to satisfy section validation rules.",
            "claimIds": ["clm-a4"] * 51,
        },
    )
    assert response.status_code == 422


def test_packet_entry_limit_is_enforced(client, monkeypatch):
    login_as(client)
    monkeypatch.setenv("ATLAS_ARGUS_MAX_PACKET_ENTRIES", "1")
    response = client.post("/api/packets", json={"packetType": "production"})
    assert response.status_code == 422
    assert "section entries" in response.json()["detail"]


def test_packet_document_limit_is_enforced(client, monkeypatch):
    login_as(client)
    monkeypatch.setenv("ATLAS_ARGUS_MAX_PACKET_DOCUMENT_BYTES", "4096")
    response = client.post("/api/packets", json={"packetType": "production"})
    assert response.status_code == 422
    assert "Packet document" in response.json()["detail"]


def test_packet_render_admission_rejects_instead_of_queueing(client, monkeypatch):
    from atlas_argus.api.app import packet_render_admission

    login_as(client)
    monkeypatch.setenv("ATLAS_ARGUS_MAX_CONCURRENT_PACKET_RENDERS", "1")
    assert packet_render_admission.try_acquire() is True
    try:
        response = client.post("/api/packets", json={"packetType": "production"})
    finally:
        packet_render_admission.release()

    assert response.status_code == 503
    assert "being rendered" in response.json()["detail"]


def test_anonymous_packet_request_does_not_consume_or_observe_render_admission(client):
    from atlas_argus.api.app import packet_render_admission

    assert packet_render_admission.try_acquire() is True
    try:
        response = client.post("/api/packets", json={"packetType": "production"})
    finally:
        packet_render_admission.release()

    assert response.status_code == 401


def test_interactive_case_integrity_rejects_history_beyond_limit(client, monkeypatch):
    from atlas_argus import services

    login_as(client)
    monkeypatch.setattr(services, "max_case_state_rows", lambda: 0)
    response = client.get("/api/cases/case-3407/audit/verify")
    assert response.status_code == 422
    assert "interactive verification limit" in response.json()["detail"]


def test_direct_packet_integrity_rejects_history_beyond_limit(client, monkeypatch):
    from atlas_argus import services

    login_as(client)
    generated = client.post("/api/packets", json={"packetType": "internal"})
    assert generated.status_code == 201
    packet_id = generated.json()["packetId"]
    monkeypatch.setattr(services, "max_packet_history_rows", lambda: 0)

    response = client.get(f"/api/packets/{packet_id}/verify")
    assert response.status_code == 422
    assert "Packet history" in response.json()["detail"]


@pytest.mark.anyio
async def test_request_body_has_a_total_receive_deadline(monkeypatch):
    app_module = importlib.import_module("atlas_argus.api.app")
    called = False

    async def downstream(scope, receive, send):
        nonlocal called
        called = True

    async def slow_receive():
        await asyncio.sleep(1)
        return {"type": "http.request", "body": b"{}", "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    monkeypatch.setattr(app_module, "request_body_timeout_seconds", lambda: 0.01)
    middleware = app_module.BodySizeLimitMiddleware(downstream)
    await middleware(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/auth/login",
            "raw_path": b"/api/auth/login",
            "query_string": b"",
            "headers": [],
            "client": ("198.51.100.10", 1234),
            "server": ("atlas.example", 443),
            "root_path": "",
        },
        slow_receive,
        send,
    )

    assert called is False
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 408


@pytest.mark.anyio
async def test_source_upload_authenticates_before_reading_body(monkeypatch):
    app_module = importlib.import_module("atlas_argus.api.app")
    receive_called = False
    downstream_called = False

    async def downstream(scope, receive, send):
        nonlocal downstream_called
        downstream_called = True

    async def receive():
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"x" * 1024, "more_body": True}

    async def send(_message):
        return None

    monkeypatch.setattr(
        app_module,
        "_source_upload_auth_failure",
        lambda _token, _case_id: (401, "Not signed in."),
    )
    middleware = app_module.BodySizeLimitMiddleware(downstream)
    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/cases/case-1/sources",
            "headers": [],
        },
        receive,
        send,
    )

    assert receive_called is False
    assert downstream_called is False


def test_source_upload_early_admission_checks_target_matter(client):
    app_module = importlib.import_module("atlas_argus.api.app")
    login_as(client)
    token = client.cookies.get("atlas_session")
    assert token is not None

    assert app_module._source_upload_auth_failure(token, "case-3407") is None
    denied = app_module._source_upload_auth_failure(token, "case-not-assigned")
    assert denied == (403, "You do not have active access to this matter.")


@pytest.mark.anyio
async def test_source_upload_body_slots_bound_authenticated_buffers(monkeypatch):
    app_module = importlib.import_module("atlas_argus.api.app")
    downstream_called = False

    async def downstream(scope, receive, send):
        nonlocal downstream_called
        downstream_called = True

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    monkeypatch.setattr(
        app_module,
        "_source_upload_auth_failure",
        lambda _token, _case_id: None,
    )
    assert app_module.source_body_admission.try_acquire() is True
    try:
        middleware = app_module.BodySizeLimitMiddleware(downstream)
        # Force the dynamic configured limit to the one slot already held.
        monkeypatch.setattr(app_module.source_body_admission, "_limit", lambda: 1)
        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/cases/case-1/sources",
                "headers": [],
            },
            receive,
            send,
        )
    finally:
        app_module.source_body_admission.release()

    assert downstream_called is False
    assert sent[0]["status"] == 503


def test_case_state_uses_batched_relationship_queries(client):
    from sqlalchemy import event

    from atlas_argus.db.session import engine

    login_as(client)
    statements: list[str] = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        response = client.get("/api/cases/case-3407")
    finally:
        event.remove(engine, "before_cursor_execute", observe)

    assert response.status_code == 200
    assert len(statements) <= 20, "case serialization regressed to relationship N+1 queries"
