from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

from conftest import login_as
from sqlalchemy import text

from atlas_argus.db.models import ReportSectionRevision
from atlas_argus.db.session import SessionLocal


def _generate(client, packet_type: str = "production") -> dict:
    response = client.post("/api/packets", json={"packetType": packet_type})
    assert response.status_code == 201, response.text
    return response.json()


def _tamper_packet(packet_id: str) -> None:
    with SessionLocal() as session, session.begin():
        session.execute(
            text("ALTER TABLE packet_artifact DISABLE TRIGGER trg_packet_artifact_append_only")
        )
        try:
            session.execute(
                text(
                    "UPDATE packet_artifact "
                    "SET document = replace(document, 'PRODUCTION COPY', 'TAMPERED COPY') "
                    "WHERE id = :id"
                ),
                {"id": packet_id},
            )
        finally:
            session.execute(
                text(
                    "ALTER TABLE packet_artifact ENABLE TRIGGER trg_packet_artifact_append_only"
                )
            )


def test_packet_verification_survives_generated_at_reordering(client, monkeypatch):
    """Reproduces the race without faking a threading race: generate_packet_op
    captures ``now = _now()`` before acquiring the table lock that serializes
    chain-link order, so a slow request that started first can still commit
    (and thus link into the chain) after a fast one that started later —
    carrying an *earlier* generated_at than the packet it's chained after.
    Each packet's own integrity_hash is computed over its own true
    generated_at, so this isn't tampering; verification must still key off
    ``seq`` (assigned at INSERT time under the lock), not generated_at."""
    import atlas_argus.services as services_module

    login_as(client)
    first = _generate(client, "production")

    earlier = datetime.now(UTC) - timedelta(hours=1)
    monkeypatch.setattr(services_module, "_now", lambda: earlier)
    second = _generate(client, "internal")
    monkeypatch.undo()

    assert second["generatedAt"] < first["generatedAt"]

    detail = client.get(f"/api/cases/case-3407/packets/{second['packetId']}")
    assert detail.status_code == 200
    assert detail.json()["verification"]["ok"] is True

    listing = client.get("/api/cases/case-3407/packets").json()
    assert listing["verification"]["ok"] is True
    assert all(p["integrityOk"] for p in listing["packets"])


def test_packet_list_and_detail_return_stored_document_with_verification(client):
    login_as(client)
    generated = _generate(client)

    listing = client.get("/api/cases/case-3407/packets")
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["verification"]["ok"] is True
    assert body["packets"][0]["packetId"] == generated["packetId"]
    assert body["packets"][0]["integrityOk"] is True

    detail = client.get(f"/api/cases/case-3407/packets/{generated['packetId']}")
    assert detail.status_code == 200
    packet = detail.json()
    assert packet["document"] == generated["document"]
    assert packet["bodySha256"] == generated["sha256"]
    assert packet["verification"]["ok"] is True
    assert packet["documentSha256"] == hashlib.sha256(packet["document"].encode()).hexdigest()


def test_packet_list_is_paginated(client):
    login_as(client)
    first = _generate(client, "production")
    second = _generate(client, "internal")

    page = client.get("/api/cases/case-3407/packets?limit=1&offset=0")
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 2
    assert body["limit"] == 1
    assert len(body["packets"]) == 1
    assert body["packets"][0]["packetId"] == second["packetId"]

    next_page = client.get("/api/cases/case-3407/packets?limit=1&offset=1").json()
    assert next_page["packets"][0]["packetId"] == first["packetId"]


def test_packet_retrieval_requires_verified_mfa_session(client):
    response = client.post(
        "/api/auth/login", json={"username": "mokafor", "password": "argus-demo"}
    )
    assert response.status_code == 200
    blocked = client.get("/api/cases/case-3407/packets")
    assert blocked.status_code == 403


def test_packet_retrieval_rejects_wrong_case(client):
    login_as(client)
    generated = _generate(client)
    response = client.get(f"/api/cases/not-this-case/packets/{generated['packetId']}")
    assert response.status_code == 404


def test_internal_packet_view_requires_privilege_clearance(client):
    login_as(client)
    generated = _generate(client, "internal")

    login_as(client, "pnatarajan")  # Claims Reviewer — not privilege-cleared
    blocked = client.get(f"/api/cases/case-3407/packets/{generated['packetId']}")
    assert blocked.status_code == 403

    login_as(client)  # Senior Aviation Counsel — privilege-cleared
    allowed = client.get(f"/api/cases/case-3407/packets/{generated['packetId']}")
    assert allowed.status_code == 200
    assert allowed.json()["document"] == generated["document"]


def test_production_packet_view_does_not_require_privilege_clearance(client):
    login_as(client)
    generated = _generate(client, "production")

    login_as(client, "pnatarajan")  # Claims Reviewer — not privilege-cleared
    allowed = client.get(f"/api/cases/case-3407/packets/{generated['packetId']}")
    assert allowed.status_code == 200


def test_noncleared_production_packet_has_no_privileged_metadata(client):
    login_as(client)
    privileged_claim_response = client.post(
        "/api/claims",
        json={
            "sourceDocumentId": "src-memo",
            "text": "SENTINEL privileged claim text for packet leakage testing.",
            "pageRef": "SENTINEL privileged page reference",
            "quote": "SENTINEL privileged quotation from confidential counsel work product.",
            "confidence": 0.91,
            "evidenceQuality": "primary",
        },
    )
    assert privileged_claim_response.status_code == 201, privileged_claim_response.text
    privileged_claim = privileged_claim_response.json()["claim"]

    public_claim_response = client.post(
        "/api/claims",
        json={
            "sourceDocumentId": "src-fdr",
            "text": "SENTINEL public claim retained for production traceability.",
            "pageRef": "FDR sentinel channel 41",
            "quote": "The public recorder channel confirms the sentinel production fact.",
            "confidence": 0.93,
            "evidenceQuality": "primary",
        },
    )
    assert public_claim_response.status_code == 201, public_claim_response.text
    public_claim = public_claim_response.json()["claim"]

    conflict_response = client.post(
        "/api/conflicts",
        json={
            "claimAId": privileged_claim["id"],
            "claimBId": public_claim["id"],
            "conflictType": "interpretation",
            "severity": "high",
            "summary": "SENTINEL privileged conflict summary must never be produced.",
        },
    )
    assert conflict_response.status_code == 201, conflict_response.text
    conflict = conflict_response.json()["conflict"]
    decision = client.post(
        f"/api/conflicts/{conflict['id']}/decisions",
        json={
            "decisionType": "preserve_both",
            "reasoning": "Both records remain preserved while production withholds privileged work product.",
            "expectedVersion": conflict["version"],
        },
    )
    assert decision.status_code == 201, decision.text

    public_section_response = client.post(
        "/api/report-sections",
        json={
            "title": "SENTINEL public included section",
            "paragraphRef": "PUBLIC-SENTINEL-1",
            "text": "This public section remains traceable while its counterpart is withheld.",
            "claimIds": [public_claim["id"]],
        },
    )
    assert public_section_response.status_code == 201, public_section_response.text
    public_section = public_section_response.json()["section"]
    public_approval = client.post(
        f"/api/report-sections/{public_section['id']}/approve",
        params={"expectedVersion": public_section["version"]},
    )
    assert public_approval.status_code == 200, public_approval.text

    privileged_section_response = client.post(
        "/api/report-sections",
        json={
            "title": "SENTINEL privileged section title",
            "paragraphRef": "PRIV-SENTINEL-9",
            "text": "SENTINEL privileged section body must never reach production.",
            "claimIds": [privileged_claim["id"]],
        },
    )
    assert privileged_section_response.status_code == 201, privileged_section_response.text
    privileged_section = privileged_section_response.json()["section"]
    privileged_approval = client.post(
        f"/api/report-sections/{privileged_section['id']}/approve",
        params={"expectedVersion": privileged_section["version"]},
    )
    assert privileged_approval.status_code == 200, privileged_approval.text
    privileged_revision_id = privileged_approval.json()["section"]["activeRevisionId"]
    with SessionLocal() as session:
        privileged_revision = session.get(ReportSectionRevision, privileged_revision_id)
        assert privileged_revision is not None
        privileged_revision_hash = privileged_revision.content_sha256
        assert privileged_revision_hash is not None

    generated = _generate(client, "production")
    login_as(client, "pnatarajan")

    detail_response = client.get(f"/api/cases/case-3407/packets/{generated['packetId']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    listing = client.get("/api/cases/case-3407/packets").json()
    summary = next(
        packet for packet in listing["packets"] if packet["packetId"] == generated["packetId"]
    )
    for payload in (detail, summary):
        assert payload["generatedByReviewerId"] is None
        assert payload["generatedByName"] is None
        assert payload["generatedByRole"] is None

    serialized_artifact = json.dumps(
        {"manifest": detail["manifest"], "document": detail["document"]},
        sort_keys=True,
    )
    forbidden = [
        privileged_section["id"],
        privileged_section["title"],
        privileged_section["paragraphRef"],
        privileged_section["text"],
        privileged_claim["id"],
        privileged_claim["text"],
        privileged_claim["pageRef"],
        privileged_claim["quote"],
        conflict["id"],
        conflict["summary"],
        privileged_revision_id,
        privileged_revision_hash,
        "rev-okafor",
        "Miriam Okafor",
        "Senior Aviation Counsel",
    ]
    for sentinel in forbidden:
        assert sentinel not in serialized_artifact

    manifest = detail["manifest"]
    assert privileged_claim["id"] not in manifest["citedClaimIds"]
    assert public_claim["id"] in manifest["citedClaimIds"]
    included_entry = next(
        entry for entry in manifest["entries"] if entry["sectionId"] == public_section["id"]
    )
    assert included_entry["paragraphRef"] == public_section["paragraphRef"]
    assert included_entry["claimIds"] == [public_claim["id"]]
    for entry in manifest["entries"]:
        if entry["disposition"] == "included":
            continue
        assert re.fullmatch(r"(?:WITHHELD|EXCLUDED)-\d{3}", entry["sectionId"])
        assert entry["paragraphRef"] == entry["sectionId"]
        assert entry["title"] in {"Withheld material", "Excluded material"}
        assert "revisionId" not in entry
        assert "revisionContentSha256" not in entry
        assert "claimIds" not in entry

    assert "Conflict disclosure — Counterpart withheld under privilege" in detail["document"]
    assert re.search(r"WITHHELD-\d{3}", detail["document"])
    assert "Internal Litigation Strategy Memorandum" in detail["document"]


def test_packet_retrieval_reports_tampered_artifact(client):
    login_as(client)
    generated = _generate(client)
    _tamper_packet(generated["packetId"])

    detail = client.get(f"/api/cases/case-3407/packets/{generated['packetId']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["verification"]["ok"] is False
    issue_kinds = {issue["kind"] for issue in body["verification"]["chain"]["issues"]}
    assert "packet_document_hash_mismatch" in issue_kinds

    listing = client.get("/api/cases/case-3407/packets").json()
    assert listing["verification"]["ok"] is False
    assert listing["packets"][0]["integrityOk"] is False
