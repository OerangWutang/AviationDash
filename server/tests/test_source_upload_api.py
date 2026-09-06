"""Upload a real PDF over the API, cite it, and verify the quote.

These exercise the whole path — middleware admission, two-transaction upload,
extraction, verification, and audit — against a real Postgres and a real
subprocess, because the interesting failures live in the seams between them.
"""

from __future__ import annotations

import base64

import pytest

from conftest import login_as
from test_ingestion import NATIVE_BODY, SECOND_BODY, _native_pdf, _scanned_pdf

CASE_ID = "case-3407"


def _upload_body(pdf: bytes, **overrides) -> dict:
    body = {
        "title": "NTSB Factual Report",
        "type": "official_report",
        "origin": "NTSB",
        "custodian": "Records Custodian",
        "docketRef": "DCA09MA027",
        "privilegeStatus": "public",
        "originalFilename": "factual-report.pdf",
        "contentBase64": base64.b64encode(pdf).decode("ascii"),
    }
    body.update(overrides)
    return body


def _upload(client, pdf: bytes = None, **overrides):
    return client.post(
        f"/api/cases/{CASE_ID}/sources",
        json=_upload_body(pdf if pdf is not None else _native_pdf([NATIVE_BODY]), **overrides),
    )


@pytest.fixture
def senior(client):
    """Senior Aviation Counsel on the sample matter, MFA-verified."""
    login_as(client, "mokafor")
    return client


@pytest.fixture
def second_senior():
    """Promote a second reviewer to Senior Aviation Counsel in this matter.

    The sample matter deliberately ships with exactly one Senior, so the
    separation-of-duties rule cannot be exercised without adding another.
    Production has the same requirement for a different reason: MFA recovery
    needs two independently controlled Senior accounts.
    """
    from sqlalchemy import update

    from atlas_argus.db import models as m
    from atlas_argus.db.session import SessionLocal

    with SessionLocal() as session, session.begin():
        session.execute(
            update(m.CaseMember)
            .where(
                m.CaseMember.case_id == CASE_ID,
                m.CaseMember.reviewer_id == "rev-reeves",
            )
            .values(role="Senior Aviation Counsel", is_active=True)
        )
    return "dreeves"


# ── happy path ─────────────────────────────────────────────────────────────


def test_upload_extracts_pages_and_records_authoritative_hash(senior):
    response = _upload(senior)
    assert response.status_code == 201, response.text
    payload = response.json()
    source = payload["source"]

    assert source["isIngested"] is True
    assert source["pageCount"] == 1
    # The hash of the actual bytes, not the legacy display column.
    import hashlib

    expected = hashlib.sha256(_native_pdf([NATIVE_BODY])).hexdigest()
    assert source["contentSha256"] == expected
    assert payload["auditEvent"]["action"] == "source ingested"
    assert expected in payload["auditEvent"]["detail"]


def test_custody_is_built_by_the_server_not_the_client(senior):
    """Custody is what makes a document admissible; a request body must not be
    able to assert who handled it."""
    response = _upload(senior, custody=[{"at": "1999-01-01T00:00:00Z", "actor": "Someone else"}])
    assert response.status_code == 201
    custody = response.json()["source"]["custody"]
    assert len(custody) == 1
    assert custody[0]["actor"] == "Miriam Okafor"
    assert "web upload" in custody[0]["action"]
    assert "1999" not in custody[0]["at"]


def test_filename_is_sanitized(senior):
    response = _upload(senior, originalFilename="../../../etc/passwd\x00.pdf")
    assert response.status_code == 201
    stored = response.json()["source"]["originalFilename"]
    assert "/" not in stored and ".." not in stored and "\x00" not in stored


def test_pages_are_listable_with_extraction_metadata(senior):
    source_id = _upload(senior, pdf=_native_pdf([NATIVE_BODY, SECOND_BODY])).json()["source"]["id"]
    response = senior.get(f"/api/cases/{CASE_ID}/sources/{source_id}/pages")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    first = payload["pages"][0]
    assert first["pageNumber"] == 1
    assert first["extractionMethod"] == "native"
    assert "stick shaker" in first["text"]
    # A pre-fill, not a citation: the reviewer owns the real page reference.
    assert first["defaultPageRef"] == "PDF page 1"


def test_original_pdf_is_viewable_with_integrity_headers(senior):
    import hashlib

    pdf = _native_pdf([NATIVE_BODY, SECOND_BODY])
    source_id = _upload(senior, pdf=pdf).json()["source"]["id"]

    response = senior.get(f"/api/cases/{CASE_ID}/sources/{source_id}/file")

    assert response.status_code == 200, response.text
    assert response.content == pdf
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-content-sha256"] == hashlib.sha256(pdf).hexdigest()
    assert response.headers["content-disposition"].startswith("inline;")
    assert "factual-report.pdf" in response.headers["content-disposition"]


def test_original_pdf_uses_the_source_visibility_anti_oracle(client):
    login_as(client, "mokafor")
    upload = _upload(client, privilegeStatus="attorney_client")
    assert upload.status_code == 201, upload.text
    source_id = upload.json()["source"]["id"]

    login_as(client, "pnatarajan")
    response = client.get(f"/api/cases/{CASE_ID}/sources/{source_id}/file")

    assert response.status_code == 404
    assert response.json()["detail"] == "Source document not found."


def test_original_pdf_is_refused_when_stored_bytes_were_rewritten(senior):
    from sqlalchemy import text as sql

    from atlas_argus.db.session import SessionLocal

    source_id, _page = _ingest_and_page(senior)
    with SessionLocal() as session, session.begin():
        session.execute(sql("ALTER TABLE source_document_file DISABLE TRIGGER USER"))
        session.execute(
            sql("UPDATE source_document_file SET content = :c WHERE source_document_id = :s"),
            {"c": b"%PDF-1.4 substituted", "s": source_id},
        )
        session.execute(sql("ALTER TABLE source_document_file ENABLE TRIGGER USER"))

    response = senior.get(f"/api/cases/{CASE_ID}/sources/{source_id}/file")

    assert response.status_code == 403
    assert "failed its integrity check" in response.json()["detail"]


# ── quote verification ─────────────────────────────────────────────────────


def _claim_body(source_id: str, page_id: str, quote: str) -> dict:
    return {
        "sourceDocumentId": source_id,
        "sourcePageExtractionId": page_id,
        "text": "The stick shaker activated before the upset.",
        "pageRef": "NTSB-000184",
        "quote": quote,
        "confidence": 0.9,
        "evidenceQuality": "primary",
    }


def _ingest_and_page(client, pdf=None):
    source_id = _upload(client, pdf=pdf).json()["source"]["id"]
    pages = client.get(f"/api/cases/{CASE_ID}/sources/{source_id}/pages").json()["pages"]
    return source_id, pages[0]


def test_quote_present_on_a_native_page_is_verified_exact(senior):
    source_id, page = _ingest_and_page(senior)
    response = senior.post(
        f"/api/cases/{CASE_ID}/claims",
        json=_claim_body(source_id, page["id"], "the stick shaker activated at 2216:27"),
    )
    assert response.status_code == 201, response.text
    claim = response.json()["claim"]
    assert claim["quoteVerification"] == "verified_exact"
    assert claim["sourcePageExtractionId"] == page["id"]


def test_fabricated_quote_on_a_clean_page_is_rejected(senior):
    """The point of the feature: a quote that is not in the evidence cannot be
    saved at all."""
    source_id, page = _ingest_and_page(senior)
    response = senior.post(
        f"/api/cases/{CASE_ID}/claims",
        json=_claim_body(
            source_id, page["id"], "the captain reported the autopilot was engaged"
        ),
    )
    assert response.status_code == 422, response.text
    assert "does not appear" in response.json()["detail"]


def test_quote_matching_tolerates_typography(senior):
    source_id, page = _ingest_and_page(senior)
    response = senior.post(
        f"/api/cases/{CASE_ID}/claims",
        json=_claim_body(source_id, page["id"], "The  Stick  Shaker  Activated  At  2216:27"),
    )
    assert response.status_code == 201, response.text
    assert response.json()["claim"]["quoteVerification"] == "verified_exact"


def test_claim_against_a_legacy_source_is_unchanged(senior):
    """Seeded sources predate ingestion; their claims must keep working and be
    labelled honestly rather than blocked."""
    response = senior.post(
        f"/api/cases/{CASE_ID}/claims",
        json={
            "sourceDocumentId": "src-ntsb",
            "text": "A claim against a legacy seeded source document.",
            "pageRef": "p. 12",
            "quote": "anything at all, since nothing can check it",
            "confidence": 0.5,
            "evidenceQuality": "secondary",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["claim"]["quoteVerification"] == "legacy_unverified"


def test_claim_cannot_cite_a_page_from_another_source(senior):
    source_a, page_a = _ingest_and_page(senior)
    source_b, _ = _ingest_and_page(senior, pdf=_native_pdf([SECOND_BODY]))
    response = senior.post(
        f"/api/cases/{CASE_ID}/claims",
        json=_claim_body(source_b, page_a["id"], "the stick shaker activated at 2216:27"),
    )
    assert response.status_code == 422
    assert "does not belong" in response.json()["detail"]


def test_client_cannot_assert_its_own_verification_state(senior):
    """``quoteVerification`` is the server's conclusion. A client supplying it
    must not have it honoured."""
    source_id, page = _ingest_and_page(senior)
    body = _claim_body(source_id, page["id"], "the stick shaker activated at 2216:27")
    body["quoteVerification"] = "manually_verified"
    response = senior.post(f"/api/cases/{CASE_ID}/claims", json=body)
    assert response.status_code == 201
    assert response.json()["claim"]["quoteVerification"] == "verified_exact"


# ── unreadable pages route to attestation, never silence ───────────────────


def test_unextractable_page_yields_attestation_not_a_silent_pass(senior):
    """Without tesseract installed this is the OCR-unavailable path; with it,
    the page extracts. Either way the claim must never come back verified
    without something having actually checked it."""
    source_id, page = _ingest_and_page(senior, pdf=_scanned_pdf(NATIVE_BODY))
    response = senior.post(
        f"/api/cases/{CASE_ID}/claims",
        json=_claim_body(source_id, page["id"], "the stick shaker activated at 2216:27"),
    )
    assert response.status_code in (201, 422)
    if response.status_code == 201:
        state = response.json()["claim"]["quoteVerification"]
        if page["extractionMethod"] == "failed":
            assert state == "manual_verification_required"
            assert page["failureCode"] is not None


def test_pending_attestation_blocks_report_eligibility(senior):
    source_id, page = _ingest_and_page(senior, pdf=_scanned_pdf(NATIVE_BODY))
    if page["extractionMethod"] != "failed":
        pytest.skip("page extracted successfully; covered by the OCR tests")
    claim = senior.post(
        f"/api/cases/{CASE_ID}/claims",
        json=_claim_body(source_id, page["id"], "any quote at all"),
    ).json()["claim"]
    assert claim["quoteVerification"] == "manual_verification_required"
    assert claim["reportEligibility"] != "eligible"


# ── manual verification: separation of duties ──────────────────────────────


def _claim_awaiting_attestation(client):
    source_id, page = _ingest_and_page(client, pdf=_scanned_pdf(NATIVE_BODY))
    if page["extractionMethod"] != "failed":
        pytest.skip("page extracted successfully; nothing awaits attestation")
    claim = client.post(
        f"/api/cases/{CASE_ID}/claims",
        json=_claim_body(source_id, page["id"], "any quote at all"),
    ).json()["claim"]
    return claim


def test_author_cannot_verify_their_own_claim(senior):
    """One reviewer must not be able to both assert a quote and clear it."""
    claim = _claim_awaiting_attestation(senior)
    response = senior.post(
        f"/api/cases/{CASE_ID}/claims/{claim['id']}/verify-quote",
        json={"reasoning": "I checked this against the original exhibit myself."},
    )
    assert response.status_code == 403
    assert "other than the reviewer who created it" in response.json()["detail"]


def test_independent_senior_can_verify_and_unblock(client, second_senior):
    login_as(client, "mokafor")
    claim = _claim_awaiting_attestation(client)
    assert claim["reportEligibility"] != "eligible"

    login_as(client, second_senior)
    response = client.post(
        f"/api/cases/{CASE_ID}/claims/{claim['id']}/verify-quote",
        json={
            "reasoning": "Compared against the certified paper exhibit in the file room."
        },
    )
    assert response.status_code == 200, response.text
    verified = response.json()["claim"]
    assert verified["quoteVerification"] == "manually_verified"


def test_verification_without_privilege_clearance_is_refused(client):
    """A matter role without clearance cannot attest, even to non-privileged
    material."""
    login_as(client, "mokafor")
    claim = _claim_awaiting_attestation(client)
    login_as(client, "pnatarajan")  # Claims Reviewer
    response = client.post(
        f"/api/cases/{CASE_ID}/claims/{claim['id']}/verify-quote",
        json={"reasoning": "I looked at the document and it seems fine to me."},
    )
    assert response.status_code == 403


def test_verification_requires_substantive_reasoning(client, second_senior):
    login_as(client, "mokafor")
    claim = _claim_awaiting_attestation(client)
    login_as(client, second_senior)
    response = client.post(
        f"/api/cases/{CASE_ID}/claims/{claim['id']}/verify-quote",
        json={"reasoning": "ok"},
    )
    assert response.status_code == 422


def test_manual_verification_is_recorded_as_structured_history(client, second_senior):
    """Who attested, in what role, why, and against which immutable page —
    queryable, not buried in an audit detail string."""
    from sqlalchemy import select

    from atlas_argus.db import models as m
    from atlas_argus.db.session import SessionLocal

    login_as(client, "mokafor")
    claim = _claim_awaiting_attestation(client)
    login_as(client, second_senior)
    client.post(
        f"/api/cases/{CASE_ID}/claims/{claim['id']}/verify-quote",
        json={"reasoning": "Compared against the certified paper exhibit in the file room."},
    )

    with SessionLocal() as session:
        row = session.execute(
            select(m.ClaimQuoteVerification).where(
                m.ClaimQuoteVerification.claim_id == claim["id"]
            )
        ).scalar_one()
        assert row.event_type == "manual_verified"
        assert row.matter_role == "Senior Aviation Counsel"
        assert row.previous_verification == "manual_verification_required"
        assert row.new_verification == "manually_verified"
        assert row.source_page_extraction_id is not None
        assert "certified paper exhibit" in row.reasoning


# ── idempotency vs. duplicate content ──────────────────────────────────────


def test_retrying_with_the_same_key_returns_the_first_result(senior):
    """A multi-minute upload is exactly what a browser or proxy retries."""
    first = _upload(senior, idempotencyKey="11111111-1111-1111-1111-111111111111")
    second = _upload(senior, idempotencyKey="11111111-1111-1111-1111-111111111111")
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["source"]["id"] == first.json()["source"]["id"]
    assert second.json()["replayed"] is True


def test_reusing_a_key_for_different_content_is_a_conflict(senior):
    """Answering with the stored result would silently substitute one piece of
    evidence for another."""
    _upload(senior, idempotencyKey="22222222-2222-2222-2222-222222222222")
    response = _upload(
        senior,
        pdf=_native_pdf([SECOND_BODY]),
        idempotencyKey="22222222-2222-2222-2222-222222222222",
    )
    assert response.status_code == 409, response.text


def test_identical_bytes_create_a_separate_custody_record(senior):
    """Same document, different custodian, is a distinct evidentiary record —
    never blocked, never auto-merged, but flagged."""
    first = _upload(senior).json()
    second = _upload(senior, custodian="Opposing Counsel Production").json()
    assert second["source"]["id"] != first["source"]["id"]
    assert first["source"]["id"] in second["possibleDuplicateSourceIds"]


# ── authorization and limits ───────────────────────────────────────────────


def test_upload_requires_authentication(client):
    response = _upload(client)
    assert response.status_code in (401, 403)


def test_non_pdf_content_is_rejected(senior):
    response = _upload(senior, pdf=b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)
    assert response.status_code == 422
    assert "PDF" in response.json()["detail"]


def test_invalid_base64_is_rejected(senior):
    response = senior.post(
        f"/api/cases/{CASE_ID}/sources",
        json=_upload_body(_native_pdf([NATIVE_BODY]), contentBase64="not base64 !!!"),
    )
    assert response.status_code == 422


def test_unknown_privilege_status_is_rejected(senior):
    response = _upload(senior, privilegeStatus="super_secret")
    assert response.status_code == 422


def test_oversized_body_is_refused_before_processing(senior):
    from atlas_argus.config import max_source_request_bytes

    response = senior.post(
        f"/api/cases/{CASE_ID}/sources",
        json=_upload_body(_native_pdf([NATIVE_BODY])),
        headers={"Content-Length": str(max_source_request_bytes() + 1)},
    )
    assert response.status_code == 413


def test_a_decision_cannot_make_an_unverified_claim_report_eligible(client, second_senior):
    """Every decision path recomputes eligibility. If that path forgot the
    quote state, resolving a conflict in a claim's favour would hand it
    report eligibility while nothing had confirmed its quote."""
    from sqlalchemy import select

    from atlas_argus.db import models as m
    from atlas_argus.db.session import SessionLocal

    login_as(client, "mokafor")
    claim = _claim_awaiting_attestation(client)

    # Drive the claim to 'supported' the way a decision would, then recompute
    # through the shared helper every decision uses.
    from atlas_argus import services

    with SessionLocal() as session, session.begin():
        row = session.execute(
            select(m.Claim).where(m.Claim.id == claim["id"])
        ).scalar_one()
        row.status = "supported"
        services._recompute_eligibility(session, row)
        assert row.quote_verification == "manual_verification_required"
        assert row.report_eligibility != "eligible", (
            "a claim whose quote nothing confirmed became report-eligible"
        )


def test_verified_claim_does_become_eligible_once_supported(client):
    """The counterpart: verification must not block a legitimately supported
    claim, or the gate would just break normal work."""
    from sqlalchemy import select

    from atlas_argus import services
    from atlas_argus.db import models as m
    from atlas_argus.db.session import SessionLocal

    login_as(client, "mokafor")
    source_id, page = _ingest_and_page(client)
    claim = client.post(
        f"/api/cases/{CASE_ID}/claims",
        json=_claim_body(source_id, page["id"], "the stick shaker activated at 2216:27"),
    ).json()["claim"]
    assert claim["quoteVerification"] == "verified_exact"

    with SessionLocal() as session, session.begin():
        row = session.execute(select(m.Claim).where(m.Claim.id == claim["id"])).scalar_one()
        row.status = "supported"
        services._recompute_eligibility(session, row)
        assert row.report_eligibility == "eligible"


# ── extraction integrity ───────────────────────────────────────────────────


def test_case_integrity_covers_the_extraction(senior):
    """Ingestion hashes are only useful if something recomputes them."""
    _upload(senior)
    response = senior.get(f"/api/cases/{CASE_ID}/audit/verify")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["extractions"]["runs"] >= 1
    assert payload["extractions"]["ok"] is True
    assert payload["extractions"]["issues"] == []
    assert payload["ok"] is True


def test_rewritten_page_text_is_detected(senior):
    """The append-only trigger blocks a runtime rewrite; this proves that even
    a schema-owner rewrite that got past it would still be visible."""
    from sqlalchemy import text as sql

    from atlas_argus.db.session import SessionLocal

    source_id, _page = _ingest_and_page(senior)

    with SessionLocal() as session, session.begin():
        # Disable the guard the way only a schema owner could, then tamper.
        session.execute(sql("ALTER TABLE source_page_extraction DISABLE TRIGGER USER"))
        session.execute(
            sql(
                "UPDATE source_page_extraction SET extracted_text = 'the stick shaker "
                "did not activate' WHERE source_document_id = :s"
            ),
            {"s": source_id},
        )
        session.execute(sql("ALTER TABLE source_page_extraction ENABLE TRIGGER USER"))

    payload = senior.get(f"/api/cases/{CASE_ID}/audit/verify").json()
    assert payload["extractions"]["ok"] is False
    kinds = {issue["kind"] for issue in payload["extractions"]["issues"]}
    assert "source_page_extraction" in kinds
    assert payload["ok"] is False


def test_rewritten_document_bytes_are_detected(senior):
    from sqlalchemy import text as sql

    from atlas_argus.db.session import SessionLocal

    source_id, _page = _ingest_and_page(senior)

    with SessionLocal() as session, session.begin():
        session.execute(sql("ALTER TABLE source_document_file DISABLE TRIGGER USER"))
        session.execute(
            sql(
                "UPDATE source_document_file SET content = :c WHERE source_document_id = :s"
            ),
            {"c": b"%PDF-1.4 substituted", "s": source_id},
        )
        session.execute(sql("ALTER TABLE source_document_file ENABLE TRIGGER USER"))

    payload = senior.get(f"/api/cases/{CASE_ID}/audit/verify").json()
    assert payload["extractions"]["ok"] is False
    kinds = {issue["kind"] for issue in payload["extractions"]["issues"]}
    assert "source_document_file" in kinds
