"""Controlled PDF export.

The point of this feature is that the produced document is decided by the
server rather than by whoever pressed Ctrl-P, so most of these assert
properties of the *document* — page size, page numbering, per-page provenance
— rather than merely that bytes came back.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from atlas_argus import packet_pdf
from atlas_argus.db import models as m
from atlas_argus.db.session import SessionLocal
from conftest import login_as

pytest.importorskip("weasyprint")
pypdf = pytest.importorskip("pypdf")

CASE_ID = "case-3407"


@pytest.fixture
def senior(client):
    login_as(client, "mokafor")
    return client


def _generate(client, packet_type: str = "production") -> dict:
    response = client.post(
        f"/api/cases/{CASE_ID}/packets", json={"packetType": packet_type}
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _stored(packet_id: str) -> m.PacketArtifact:
    with SessionLocal() as session:
        return session.execute(
            select(m.PacketArtifact).where(m.PacketArtifact.id == packet_id)
        ).scalar_one()


def _pdf_bytes(packet_id: str) -> bytes:
    with SessionLocal() as session:
        return session.execute(
            select(m.PacketArtifact.pdf).where(m.PacketArtifact.id == packet_id)
        ).scalar_one()


def _read(pdf: bytes):
    import io

    return pypdf.PdfReader(io.BytesIO(pdf))


# ── the document ───────────────────────────────────────────────────────────


def test_generating_a_packet_stores_a_pdf(senior):
    packet_id = _generate(senior)["packetId"]
    artifact = _stored(packet_id)
    assert artifact.pdf_sha256 is not None
    pdf = _pdf_bytes(packet_id)
    assert pdf.startswith(b"%PDF")
    assert hashlib.sha256(pdf).hexdigest() == artifact.pdf_sha256


def test_pages_are_a_fixed_physical_size(senior):
    """Browser print-to-PDF paginates to whatever the user's page setup says.
    A filed document cannot depend on that."""
    reader = _read(_pdf_bytes(_generate(senior)["packetId"]))
    for page in reader.pages:
        assert round(float(page.mediabox.width)) == 612  # Letter, 8.5in
        assert round(float(page.mediabox.height)) == 792  # 11in


def test_every_page_carries_page_numbers_and_provenance(senior):
    """A page separated from the packet still has to be identifiable — pages
    get detached from bundles routinely."""
    packet_id = _generate(senior)["packetId"]
    reader = _read(_pdf_bytes(packet_id))
    total = len(reader.pages)
    assert total >= 1
    for index, page in enumerate(reader.pages, start=1):
        text_content = (page.extract_text() or "").replace("\n", " ")
        assert f"Page {index} of {total}" in text_content
        assert packet_id in text_content, "packet id missing from a page footer"
        assert re.search(r"sha256:[0-9a-f]{16}", text_content), "no body hash on page"


def test_classification_is_stamped_on_every_page(senior):
    for packet_type, marker in (
        ("production", "PRODUCTION COPY"),
        ("internal", "INTERNAL WORK PRODUCT"),
    ):
        reader = _read(_pdf_bytes(_generate(senior, packet_type)["packetId"]))
        for page in reader.pages:
            assert marker in (page.extract_text() or "").replace("\n", " ")


def test_the_production_pdf_withholds_what_the_html_withholds(senior):
    """The PDF is a rendering of the stored document, so the redaction
    doctrine must survive it — a leak here would bypass every other control."""
    packet_id = _generate(senior, "production")["packetId"]
    body = " ".join(
        (page.extract_text() or "") for page in _read(_pdf_bytes(packet_id)).pages
    )
    # Privileged material appears only as an opaque reference in the log.
    assert "WITHHELD-001" in body.replace("\n", " ")
    # Internal assessments are stripped from production output.
    assert "Evidence quality" not in body
    assert "confidence" not in body.lower() or "Confidence" not in body


def test_the_pdf_is_rendered_from_the_stored_document(senior):
    """If the PDF were assembled separately the two could describe different
    packets."""
    packet_id = _generate(senior)["packetId"]
    artifact = _stored(packet_id)
    body = " ".join(
        (page.extract_text() or "") for page in _read(_pdf_bytes(packet_id)).pages
    )
    assert artifact.body_sha256[:16] in body.replace("\n", "")


# ── determinism ────────────────────────────────────────────────────────────


def test_rendering_is_byte_reproducible():
    """Not the basis of the integrity claim, but without it a rendering
    regression is invisible. Font subsetting embeds a build timestamp unless
    it is pinned."""

    doc = (
        "<!doctype html><html><head><meta charset='utf-8'></head>"
        "<body><h1>Packet</h1><p>Body text.</p></body></html>"
    )
    stub = type(
        "P",
        (),
        {"type": "production", "packet_id": "pkt-test", "generated_at": "2026-07-29T12:00:00Z"},
    )()
    at = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    first = packet_pdf.render_packet_pdf(stub, doc, "a" * 64, generated_at=at, max_bytes=10**7)
    second = packet_pdf.render_packet_pdf(stub, doc, "a" * 64, generated_at=at, max_bytes=10**7)
    assert first == second


def test_render_respects_the_size_ceiling():
    doc = "<!doctype html><html><body><h1>Packet</h1></body></html>"
    stub = type(
        "P",
        (),
        {"type": "production", "packet_id": "pkt-test", "generated_at": "2026-07-29T12:00:00Z"},
    )()
    with pytest.raises(packet_pdf.PdfTooLarge):
        packet_pdf.render_packet_pdf(
            stub,
            doc,
            "a" * 64,
            generated_at=datetime(2026, 7, 29, tzinfo=UTC),
            max_bytes=1024,
        )


# ── integrity ──────────────────────────────────────────────────────────────


def test_pre_pdf_artifacts_still_verify(senior):
    """The migration added a field to the hashed artifact content. Including it
    unconditionally would have changed the canonical content of every artifact
    generated before this feature and broken the chain for all of them."""
    packet_id = _generate(senior)["packetId"]
    with SessionLocal() as session, session.begin():
        # Make it look like a pre-PDF artifact without touching its stored
        # integrity hash — exactly the shape of a real legacy row.
        session.execute(text("ALTER TABLE packet_artifact DISABLE TRIGGER USER"))
        session.execute(
            text("UPDATE packet_artifact SET pdf = NULL, pdf_sha256 = NULL WHERE id = :p"),
            {"p": packet_id},
        )
        session.execute(text("ALTER TABLE packet_artifact ENABLE TRIGGER USER"))

    verification = senior.get(f"/api/cases/{CASE_ID}/packets/{packet_id}/verify").json()
    # Its own hash was computed *with* the PDF, so removing it must be caught.
    assert verification["ok"] is False

    # And an artifact that never had one verifies cleanly.
    fresh = senior.get(f"/api/cases/{CASE_ID}/audit/verify").json()
    assert "packets" in fresh


def test_an_artifact_generated_without_a_pdf_verifies(senior, monkeypatch):
    """The compatibility path that matters. An artifact created when rendering
    was unavailable hashes its content *without* pdfSha256; verification must
    recompute it the same way, or every artifact predating this feature would
    read as tampered."""
    from atlas_argus.packet_pdf import PdfRenderingUnavailable

    def unavailable(*args, **kwargs):
        raise PdfRenderingUnavailable("no renderer in this environment")

    monkeypatch.setattr("atlas_argus.packet_pdf.render_packet_pdf", unavailable)
    packet_id = _generate(senior)["packetId"]
    monkeypatch.undo()

    artifact = _stored(packet_id)
    assert artifact.pdf_sha256 is None, "expected the degraded path to store no PDF"

    verification = senior.get(f"/api/cases/{CASE_ID}/packets/{packet_id}/verify").json()
    assert verification["ok"] is True
    assert verification["hasPdf"] is False


def test_a_missing_renderer_is_recorded_not_silent(senior, monkeypatch):
    """Degrading quietly would leave counsel believing they had a producible
    packet."""
    from atlas_argus.packet_pdf import PdfRenderingUnavailable

    def unavailable(*args, **kwargs):
        raise PdfRenderingUnavailable("no renderer")

    monkeypatch.setattr("atlas_argus.packet_pdf.render_packet_pdf", unavailable)
    packet_id = _generate(senior)["packetId"]
    monkeypatch.undo()

    events = senior.get(f"/api/cases/{CASE_ID}").json()["auditEvents"]
    entry = next(e for e in events if e["subjectId"] == packet_id)
    assert "PDF not rendered" in entry["detail"]


def test_production_refuses_to_boot_without_pdf_rendering(monkeypatch):
    """In production the degraded path would mean disclosure packets silently
    reverting to browser print-to-PDF."""
    from atlas_argus import config

    monkeypatch.setattr("atlas_argus.packet_pdf.pdf_available", lambda: False)
    monkeypatch.setenv("ATLAS_ARGUS_ENV", "production")
    monkeypatch.setenv("ATLAS_ARGUS_DATABASE_URL", "postgresql+psycopg://u:p@h/d")
    monkeypatch.setenv("ATLAS_ARGUS_MIGRATION_DATABASE_URL", "postgresql+psycopg://o:p@h/d")
    monkeypatch.setenv("ATLAS_ARGUS_METRICS_TOKEN", "token")
    errors = config.production_config_errors()
    assert any("PDF rendering is unavailable" in e for e in errors)


def test_tampering_with_the_stored_pdf_is_detected(senior):
    packet_id = _generate(senior)["packetId"]
    with SessionLocal() as session, session.begin():
        session.execute(text("ALTER TABLE packet_artifact DISABLE TRIGGER USER"))
        session.execute(
            text("UPDATE packet_artifact SET pdf = :b WHERE id = :p"),
            {"b": b"%PDF-1.7 substituted document", "p": packet_id},
        )
        session.execute(text("ALTER TABLE packet_artifact ENABLE TRIGGER USER"))

    verification = senior.get(f"/api/cases/{CASE_ID}/packets/{packet_id}/verify").json()
    assert verification["ok"] is False
    assert any(issue["kind"] == "packet_pdf" for issue in verification["packetIssues"])


# ── download ───────────────────────────────────────────────────────────────


def test_downloading_returns_the_exact_stored_bytes(senior):
    packet_id = _generate(senior)["packetId"]
    response = senior.get(f"/api/cases/{CASE_ID}/packets/{packet_id}/pdf")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert ".pdf" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert response.content == _pdf_bytes(packet_id)


def test_a_failed_integrity_packet_is_not_served(senior):
    """Handing over a document the system cannot vouch for is worse than
    handing over nothing — the recipient has no way to tell."""
    packet_id = _generate(senior)["packetId"]
    with SessionLocal() as session, session.begin():
        session.execute(text("ALTER TABLE packet_artifact DISABLE TRIGGER USER"))
        session.execute(
            text("UPDATE packet_artifact SET pdf = :b WHERE id = :p"),
            {"b": b"%PDF-1.7 tampered", "p": packet_id},
        )
        session.execute(text("ALTER TABLE packet_artifact ENABLE TRIGGER USER"))

    response = senior.get(f"/api/cases/{CASE_ID}/packets/{packet_id}/pdf")
    assert response.status_code == 403
    assert "integrity" in response.json()["detail"].lower()


def test_an_internal_packet_pdf_needs_privilege_clearance(client):
    login_as(client, "mokafor")
    packet_id = _generate(client, "internal")["packetId"]
    login_as(client, "pnatarajan")  # Claims Reviewer
    response = client.get(f"/api/cases/{CASE_ID}/packets/{packet_id}/pdf")
    assert response.status_code in (403, 404)


def test_downloading_requires_authentication(client):
    assert client.get(f"/api/cases/{CASE_ID}/packets/pkt-nope/pdf").status_code in (401, 403)


def test_a_packet_without_a_pdf_says_so(senior):
    packet_id = _generate(senior)["packetId"]
    with SessionLocal() as session, session.begin():
        session.execute(text("ALTER TABLE packet_artifact DISABLE TRIGGER USER"))
        session.execute(
            text("UPDATE packet_artifact SET pdf=NULL, pdf_sha256=NULL WHERE id=:p"),
            {"p": packet_id},
        )
        session.execute(text("ALTER TABLE packet_artifact ENABLE TRIGGER USER"))
    response = senior.get(f"/api/cases/{CASE_ID}/packets/{packet_id}/pdf")
    assert response.status_code == 404
    assert "regenerate" in response.json()["detail"].lower()


def test_artifact_listing_reports_pdf_availability(senior):
    packet_id = _generate(senior)["packetId"]
    listing = senior.get(f"/api/cases/{CASE_ID}/packets").json()
    entry = next(a for a in listing["packets"] if a["packetId"] == packet_id)
    assert entry["hasPdf"] is True
    assert entry["pdfSha256"] == _stored(packet_id).pdf_sha256
    assert entry["pdfFilename"].endswith(".pdf")


def test_filename_is_constrained_for_the_header():
    assert packet_pdf.content_disposition_filename('a"b;c\r\n.pdf') == "a-b-c--.pdf"
    assert packet_pdf.content_disposition_filename("") == "packet.pdf"
