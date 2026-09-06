"""Evidence-state binding for production report approval."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import models as m
from .integrity import canonical_sha256


def production_evidence_claim_ids(session: Session, claim_ids: list[str]) -> list[str]:
    """Expand cited claims to every claim rendered by preserved disclosures."""
    ordered = list(dict.fromkeys(claim_ids))
    seen = set(ordered)
    frontier = set(ordered)
    while frontier:
        conflicts = session.execute(
            select(m.Conflict).where(
                m.Conflict.status == "preserved_both",
                or_(
                    m.Conflict.claim_a_id.in_(frontier),
                    m.Conflict.claim_b_id.in_(frontier),
                ),
            )
        ).scalars()
        discovered: set[str] = set()
        for conflict in conflicts:
            for claim_id in (conflict.claim_a_id, conflict.claim_b_id):
                if claim_id not in seen:
                    seen.add(claim_id)
                    discovered.add(claim_id)
        ordered.extend(sorted(discovered))
        frontier = discovered
    return ordered


def production_evidence_claim_ids_from_conflicts(
    claim_ids: list[str], conflicts: Iterable[m.Conflict]
) -> list[str]:
    """Expand a citation set without issuing relationship queries."""
    ordered = list(dict.fromkeys(claim_ids))
    seen = set(ordered)
    frontier = set(ordered)
    preserved = [conflict for conflict in conflicts if conflict.status == "preserved_both"]
    while frontier:
        discovered = {
            claim_id
            for conflict in preserved
            if conflict.claim_a_id in frontier or conflict.claim_b_id in frontier
            for claim_id in (conflict.claim_a_id, conflict.claim_b_id)
            if claim_id not in seen
        }
        seen.update(discovered)
        ordered.extend(sorted(discovered))
        frontier = discovered
    return ordered


def evidence_state_sha256_from_loaded(
    claim_ids: list[str],
    *,
    claims_by_id: dict[str, m.Claim],
    sources_by_id: dict[str, m.SourceDocument],
    content_hash_by_source_id: dict[str, str],
    conflicts: Iterable[m.Conflict],
) -> str:
    """Compute the approval digest from a caller-owned coherent snapshot."""
    conflict_rows = list(conflicts)
    evidence_claim_ids = production_evidence_claim_ids_from_conflicts(claim_ids, conflict_rows)
    conflicts_by_claim_id: dict[str, list[m.Conflict]] = {}
    for conflict in sorted(conflict_rows, key=lambda row: row.id):
        conflicts_by_claim_id.setdefault(conflict.claim_a_id, []).append(conflict)
        conflicts_by_claim_id.setdefault(conflict.claim_b_id, []).append(conflict)

    claims: list[dict] = []
    for claim_id in evidence_claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is None:
            claims.append({"id": claim_id, "missing": True})
            continue
        source = sources_by_id.get(claim.source_document_id)
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
                        "ingestedAt": source.ingested_at.isoformat(),
                        "sha256": source.sha256,
                        "storedContentSha256": content_hash_by_source_id.get(source.id),
                        "privilegeStatus": source.privilege_status,
                        "custody": source.custody,
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
                    for conflict in conflicts_by_claim_id.get(claim.id, [])
                ],
            }
        )

    return canonical_sha256(
        {
            "schema": "atlas_argus.report_approval_evidence.v2",
            "claimIds": list(claim_ids),
            "evidenceClaimIds": evidence_claim_ids,
            "claims": claims,
        }
    )


def evidence_state_sha256(session: Session, claim_ids: list[str]) -> str:
    """Hash everything a cited-evidence approval relies on.

    Report-section revisions already bind counsel's wording and claim ids. This
    second digest binds the mutable evidentiary state behind those ids, so a
    later conflict decision, quote verification, or source/claim correction
    cannot inherit an earlier production approval.
    """
    evidence_claim_ids = production_evidence_claim_ids(session, claim_ids)
    claims = list(
        session.execute(select(m.Claim).where(m.Claim.id.in_(evidence_claim_ids))).scalars()
    )
    source_ids = {claim.source_document_id for claim in claims}
    sources = list(
        session.execute(
            select(m.SourceDocument).where(m.SourceDocument.id.in_(source_ids))
        ).scalars()
    )
    content_hashes = dict(
        session.execute(
            select(
                m.SourceDocumentFile.source_document_id,
                m.SourceDocumentFile.content_sha256,
            ).where(m.SourceDocumentFile.source_document_id.in_(source_ids))
        ).all()
    )
    conflicts = list(
        session.execute(
            select(m.Conflict).where(
                or_(
                    m.Conflict.claim_a_id.in_(evidence_claim_ids),
                    m.Conflict.claim_b_id.in_(evidence_claim_ids),
                )
            )
        ).scalars()
    )
    return evidence_state_sha256_from_loaded(
        claim_ids,
        claims_by_id={claim.id: claim for claim in claims},
        sources_by_id={source.id: source for source in sources},
        content_hash_by_source_id=content_hashes,
        conflicts=conflicts,
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


__all__ = [
    "evidence_state_sha256",
    "evidence_state_sha256_from_loaded",
    "production_evidence_claim_ids",
    "production_evidence_claim_ids_from_conflicts",
    "revision_approval_is_current",
]
