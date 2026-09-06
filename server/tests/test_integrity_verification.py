from __future__ import annotations

from conftest import login_as
from sqlalchemy import text

from atlas_argus.db.session import SessionLocal


def _tamper(table: str, trigger: str, statement: str, params: dict) -> None:
    with SessionLocal() as session, session.begin():
        session.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
        try:
            session.execute(text(statement), params)
        finally:
            session.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))


def test_case_audit_verification_detects_tampered_audit_event(client):
    login_as(client)
    ok = client.get("/api/cases/case-3407/audit/verify")
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

    _tamper(
        "audit_event",
        "trg_audit_append_only",
        "UPDATE audit_event SET detail = 'tampered audit detail' WHERE id = 'aud-001'",
        {},
    )

    tampered = client.get("/api/cases/case-3407/audit/verify")
    assert tampered.status_code == 200
    body = tampered.json()
    assert body["ok"] is False
    assert body["audit"]["ok"] is False
    assert any(
        issue["id"] == "aud-001" and issue["kind"] == "integrity_hash_mismatch"
        for issue in body["audit"]["issues"]
    )


def test_complete_case_verification_requires_privilege_clearance(client):
    login_as(client, "pnatarajan")

    response = client.get("/api/cases/case-3407/audit/verify")

    assert response.status_code == 403
    assert "complete matter evidence set" in response.json()["detail"]


def test_account_audit_verification_detects_tampering(client):
    login_as(client)
    created = client.post(
        "/api/admin/reviewers",
        json={
            "name": "Avery Chen",
            "username": "achen",
            "role": "Claims Reviewer",
            "initialPassword": "temporary-password",
        },
    )
    assert created.status_code == 201
    event_id = created.json()["accountAuditEvent"]["id"]
    ok = client.get("/api/admin/account-audit/verify")
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

    _tamper(
        "account_audit_event",
        "trg_account_audit_append_only",
        "UPDATE account_audit_event SET detail = 'tampered account detail' WHERE id = :id",
        {"id": event_id},
    )

    tampered = client.get("/api/admin/account-audit/verify")
    assert tampered.status_code == 200
    body = tampered.json()
    assert body["ok"] is False
    assert any(
        issue["id"] == event_id and issue["kind"] == "integrity_hash_mismatch"
        for issue in body["accountAudit"]["issues"]
    )


def test_packet_verification_detects_tampered_artifact_document(client):
    login_as(client)
    generated = client.post("/api/packets", json={"packetType": "production"})
    assert generated.status_code == 201
    packet = generated.json()
    assert "packetIntegrityHash" in packet

    ok = client.get(f"/api/packets/{packet['packetId']}/verify")
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

    _tamper(
        "packet_artifact",
        "trg_packet_artifact_append_only",
        "UPDATE packet_artifact SET document = replace(document, 'PRODUCTION COPY', 'TAMPERED COPY') WHERE id = :id",
        {"id": packet["packetId"]},
    )

    tampered = client.get(f"/api/packets/{packet['packetId']}/verify")
    assert tampered.status_code == 200
    body = tampered.json()
    assert body["ok"] is False
    issue_kinds = {issue["kind"] for issue in body["chain"]["issues"]}
    assert "integrity_hash_mismatch" in issue_kinds
    assert "packet_document_hash_mismatch" in issue_kinds


def test_case_verification_recomputes_report_revision_content(client):
    login_as(client)
    ok = client.get("/api/cases/case-3407/audit/verify")
    assert ok.status_code == 200
    assert ok.json()["sections"]["ok"] is True

    _tamper(
        "report_section_revision",
        "trg_report_revision_append_only",
        (
            "UPDATE report_section_revision SET text = 'tampered historical wording' "
            "WHERE id = 'rpt-acft-rev-001'"
        ),
        {},
    )

    tampered = client.get("/api/cases/case-3407/audit/verify")
    assert tampered.status_code == 200
    body = tampered.json()
    assert body["ok"] is False
    assert body["sections"]["ok"] is False
    issue_kinds = {
        issue["kind"]
        for issue in body["sections"]["issues"]
        if issue["id"] == "rpt-acft-rev-001"
    }
    assert "revision_content_hash_mismatch" in issue_kinds
    assert "integrity_hash_mismatch" in issue_kinds


def test_case_verification_covers_review_decision_history(client):
    login_as(client)
    ok = client.get("/api/cases/case-3407/audit/verify")
    assert ok.status_code == 200
    assert ok.json()["decisions"]["ok"] is True

    _tamper(
        "review_decision",
        "trg_review_decision_append_only",
        (
            "UPDATE review_decision SET reasoning = 'tampered adjudication' "
            "WHERE id = 'dec-cf4-1'"
        ),
        {},
    )

    tampered = client.get("/api/cases/case-3407/audit/verify")
    assert tampered.status_code == 200
    body = tampered.json()
    assert body["ok"] is False
    assert body["decisions"]["ok"] is False
    assert any(
        issue["id"] == "dec-cf4-1" and issue["kind"] == "integrity_hash_mismatch"
        for issue in body["decisions"]["issues"]
    )
