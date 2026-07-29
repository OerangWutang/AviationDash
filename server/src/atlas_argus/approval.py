"""Evidence-state binding for production report approval."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import models as m
from .integrity import canonical_sha256


def evidence_state_sha256(session: Session, claim_ids: list[str]) -> str:
    """Hash everything a cited-evidence approval relies on.

    Report-section revisions already bind counsel's wording and claim ids. This
    second digest binds the mutable evidentiary state behind those ids, so a
    later conflict decision, quote verification, or source/claim correction
    cannot inherit an earlier production approval.
    """
    claims: list[dict] = []
    for claim_id in claim_ids:
        claim = session.get(m.Claim, claim_id)
        if claim is None:
            claims.append({"id": claim_id, "missing": True})
            continue

        source = session.get(m.SourceDocument, claim.source_document_id)
        conflicts = list(
            session.execute(
                select(m.Conflict)
                .where(
                    or_(
                        m.Conflict.claim_a_id == claim.id,
                        m.Conflict.claim_b_id == claim.id,
                    )
                )
                .order_by(m.Conflict.id)
            ).scalars()
        )
        claims.append(
            {
                "id": claim.id,
                "caseId": claim.case_id,
                "text": claim.text,
                "sourceDocumentId": claim.source_document_id,
                "pageRef": claim.page_ref,
                "quote": claim.quote,
                "status": claim.status,
                "privilegeStatus": claim.privilege_status,
                "reportEligibility": claim.report_eligibility,
                "quoteVerification": claim.quote_verification,
                "quoteVerificationBasisSha256": claim.quote_verification_basis_sha256,
                "sourcePageExtractionId": claim.source_page_extraction_id,
                "updatedAt": claim.updated_at.isoformat(),
                "source": (
                    {
                        "id": source.id,
                        "title": source.title,
                        "type": source.type,
                        "origin": source.origin,
                        "custodian": source.custodian,
                        "docketRef": source.docket_ref,
                        "sha256": source.sha256,
                        "privilegeStatus": source.privilege_status,
                        "activeExtractionRunId": source.active_extraction_run_id,
                    }
                    if source is not None
                    else None
                ),
                "conflicts": [
                    {
                        "id": conflict.id,
                        "claimAId": conflict.claim_a_id,
                        "claimBId": conflict.claim_b_id,
                        "type": conflict.conflict_type,
                        "severity": conflict.severity,
                        "status": conflict.status,
                        "summary": conflict.summary,
                        "claimADisposition": conflict.claim_a_disposition,
                        "claimBDisposition": conflict.claim_b_disposition,
                        "version": conflict.version,
                    }
                    for conflict in conflicts
                ],
            }
        )

    return canonical_sha256(
        {
            "schema": "atlas_argus.report_approval_evidence.v1",
            "claimIds": list(claim_ids),
            "claims": claims,
        }
    )


def revision_approval_is_current(
    session: Session,
    revision: m.ReportSectionRevision | None,
) -> bool:
    return (
        revision is not None
        and revision.approval_state == "approved"
        and revision.approval_evidence_sha256 is not None
        and revision.approval_evidence_sha256
        == evidence_state_sha256(session, list(revision.claim_ids))
    )


__all__ = ["evidence_state_sha256", "revision_approval_is_current"]
