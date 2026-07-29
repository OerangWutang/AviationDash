"""Canonical integrity hashes for immutable legal artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def chain_sha256(*, previous_hash: str | None, content: Any) -> str:
    return canonical_sha256(
        {
            "schema": "atlas_argus.integrity_chain_link.v1",
            "previousHash": previous_hash,
            "content": content,
        }
    )


def revision_content_sha256(
    *,
    case_id: str,
    section_id: str,
    title: str,
    paragraph_ref: str,
    text: str,
    claim_ids: list[str],
) -> str:
    return canonical_sha256(
        {
            "schema": "atlas_argus.report_section_revision_content.v1",
            "caseId": case_id,
            "sectionId": section_id,
            "title": title,
            "paragraphRef": paragraph_ref,
            "text": text,
            "claimIds": list(claim_ids),
        }
    )


def audit_event_content(event: Any) -> dict:
    return {
        "schema": "atlas_argus.audit_event.v1",
        "id": event.id,
        "caseId": event.case_id,
        "at": iso_z(event.at),
        "actorReviewerId": event.actor_reviewer_id,
        "actor": event.actor,
        "actorRole": event.actor_role,
        "action": event.action,
        "subjectType": event.subject_type,
        "subjectId": event.subject_id,
        "previousStatus": event.previous_status,
        "newStatus": event.new_status,
        "detail": event.detail,
        "reportEligibilityChanged": event.report_eligibility_changed,
    }


def account_audit_event_content(event: Any) -> dict:
    return {
        "schema": "atlas_argus.account_audit_event.v1",
        "id": event.id,
        "at": iso_z(event.at),
        "actorReviewerId": event.actor_reviewer_id,
        "actor": event.actor,
        "actorRole": event.actor_role,
        "action": event.action,
        "subjectReviewerId": event.subject_reviewer_id,
        "previousStatus": event.previous_status,
        "newStatus": event.new_status,
        "detail": event.detail,
    }


def report_section_revision_content(revision: Any) -> dict:
    content = {
        "schema": "atlas_argus.report_section_revision_row.v1",
        "id": revision.id,
        "sectionId": revision.section_id,
        "caseId": revision.case_id,
        "title": revision.title,
        "paragraphRef": revision.paragraph_ref,
        "text": revision.text,
        "claimIds": list(revision.claim_ids),
        "authorReviewerId": revision.author_reviewer_id,
        "author": revision.author,
        "authorRole": revision.author_role,
        "createdAt": iso_z(revision.created_at),
        "revisionReason": revision.revision_reason,
        "parentRevisionId": revision.parent_revision_id,
        "approvalState": revision.approval_state,
        "contentSha256": revision.content_sha256,
    }
    approval_evidence_sha256 = getattr(revision, "approval_evidence_sha256", None)
    if approval_evidence_sha256:
        content["approvalEvidenceSha256"] = approval_evidence_sha256
    return content


def review_decision_content(decision: Any) -> dict:
    return {
        "schema": "atlas_argus.review_decision.v1",
        "id": decision.id,
        "conflictId": decision.conflict_id,
        "decisionType": decision.decision_type,
        "selectedClaimId": decision.selected_claim_id,
        "reasoning": decision.reasoning,
        "reviewerName": decision.reviewer_name,
        "reviewerRole": decision.reviewer_role,
        "createdAt": iso_z(decision.created_at),
        "previousStatus": decision.previous_status,
        "newStatus": decision.new_status,
        "reportImpact": decision.report_impact,
    }


def packet_artifact_manifest_hash(manifest: dict) -> str:
    manifest_without_hashes = dict(manifest)
    manifest_without_hashes.pop("manifestSha256", None)
    manifest_without_hashes.pop("artifactSha256", None)
    return canonical_sha256(manifest_without_hashes)


def packet_artifact_content(artifact: Any) -> dict:
    document_sha256 = hashlib.sha256(artifact.document.encode("utf-8")).hexdigest()
    manifest_sha256 = packet_artifact_manifest_hash(dict(artifact.manifest))
    artifact_sha256 = canonical_sha256(
        {
            "schema": "atlas_argus.packet_artifact.v1",
            "documentSha256": document_sha256,
            "manifestSha256": manifest_sha256,
        }
    )
    content = {
        "schema": "atlas_argus.packet_artifact_row.v1",
        "id": artifact.id,
        "caseId": artifact.case_id,
        "packetType": artifact.packet_type,
        "generatedAt": iso_z(artifact.generated_at),
        "generatedByReviewerId": artifact.generated_by_reviewer_id,
        "generatedByName": artifact.generated_by_name,
        "generatedByRole": artifact.generated_by_role,
        "filename": artifact.filename,
        "bodySha256": artifact.body_sha256,
        "documentSha256": document_sha256,
        "manifestSha256": manifest_sha256,
        "artifactSha256": artifact_sha256,
    }
    # Included only when a PDF exists, and deliberately so. Adding the key
    # unconditionally — even as null — would change the canonical content of
    # every artifact generated before PDF export existed, and therefore break
    # chain verification for all of them. Omitting it leaves those artifacts
    # byte-identical to what they hashed to originally.
    #
    # This is not a hole: the column is on an append-only, UPDATE-revoked
    # table, and clearing it on an artifact that had a PDF changes the content
    # back, which no longer matches the stored integrity_hash. The chain
    # reports it.
    pdf_hash = getattr(artifact, "pdf_sha256", None)
    if pdf_hash:
        content["pdfSha256"] = pdf_hash
    return content


def extraction_manifest(
    *,
    content_sha256: str,
    source_document_id: str,
    run: Any,
    pages: Any,
) -> dict:
    """Everything that must still hold if this extraction is to be trusted.

    Serialization rules matter as much as the contents: keys sorted, compact
    separators, explicit nulls, pages in page-number order, and integer
    confidence rather than a float — a float's textual form is not stable
    across platforms, so hashing one would produce spurious integrity failures.
    ``canonical_json`` above supplies the first two; the rest is why
    ``ocr_confidence_bps`` is stored in basis points.
    """
    ordered = sorted(pages, key=lambda page: page.page_number)
    return {
        "schema": "atlas_argus.extraction_manifest.v1",
        "contentSha256": content_sha256,
        "sourceDocumentId": source_document_id,
        "extractionRunId": run.id,
        "parserVersion": run.parser_version,
        "ocrEngineVersion": run.ocr_engine_version,
        "ocrLanguages": run.ocr_languages,
        "configSnapshot": run.config_snapshot,
        "pageCount": run.page_count,
        "ocrPageCount": run.ocr_page_count,
        "failedPageCount": run.failed_page_count,
        "pages": [
            {
                "id": page.id,
                "pageNumber": page.page_number,
                "textSha256": page.text_sha256,
                "extractionMethod": page.extraction_method,
                "ocrConfidenceBps": page.ocr_confidence_bps,
                "textTruncated": bool(page.text_truncated),
                "failureCode": page.failure_code,
            }
            for page in ordered
        ],
    }


def extraction_manifest_sha256(
    *,
    content_sha256: str,
    source_document_id: str,
    run: Any,
    pages: Any,
) -> str:
    return canonical_sha256(
        extraction_manifest(
            content_sha256=content_sha256,
            source_document_id=source_document_id,
            run=run,
            pages=pages,
        )
    )
