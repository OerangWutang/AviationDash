from __future__ import annotations

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
