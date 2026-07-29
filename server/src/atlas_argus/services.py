"""Case operations: load, decide, extract, flag, draft, export, and verify.

Each operation runs inside the caller's transaction, applies the pure domain
rules, persists the result, and appends audit events. Serialization is
camelCase to match the frontend types exactly; ``relatedConflictIds`` and
``decisionIds`` are derived here, never stored.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import auth
from .config import (
    max_packet_artifact_bytes,
    max_packet_document_bytes,
    max_packet_entries,
    max_packet_manifest_bytes,
    max_packet_pdf_bytes,
    max_source_upload_bytes,
    min_ocr_confidence_for_auto_verify,
)
from .db import models as m
from .db.seed import iso_z
from .domain import claims as claims_rules
from .domain import decisions as decision_rules
from .domain import matters as matter_rules
from .domain import quotes as quote_rules
from .domain import report as report_rules
from .domain import report_sections as section_rules
from .domain import sources as source_rules
from .domain import types as t
from .domain.eligibility import compute_claim_eligibility
from .domain.permissions import admin_permission, decision_permission, packet_permission
from .integrity import (
    account_audit_event_content,
    audit_event_content,
    canonical_json,
    canonical_sha256,
    chain_sha256,
    extraction_manifest_sha256,
    packet_artifact_content,
    packet_artifact_manifest_hash,
    report_section_revision_content,
    review_decision_content,
    revision_content_sha256,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _event_id() -> str:
    return f"aud-{uuid.uuid4().hex[:12]}"


def _reviewer_id() -> str:
    return f"rev-{uuid.uuid4().hex[:12]}"


def _revision_id() -> str:
    return f"rsr-{uuid.uuid4().hex[:12]}"


def _next_id(session: Session, sequence: str, prefix: str) -> str:
    n = session.execute(text(f"SELECT nextval('{sequence}')")).scalar_one()
    return f"{prefix}-{n}"


def _utf8_bytes(value: str) -> int:
    return len(value.encode("utf-8"))


def _transaction_advisory_lock(session: Session, *, chain: str, scope: str) -> None:
    """Serialize one append chain without requiring table-owner privileges."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
        {"lock_name": f"atlas_argus:{chain}:{scope}"},
    )


def _lock_audit_chain(session: Session, case_id: str) -> None:
    _transaction_advisory_lock(session, chain="audit_event", scope=case_id)


def _lock_account_audit_chain(session: Session) -> None:
    _transaction_advisory_lock(session, chain="account_audit_event", scope="global")


def _lock_report_revision_chain(session: Session, section_id: str) -> None:
    _transaction_advisory_lock(session, chain="report_section_revision", scope=section_id)


def _lock_report_section_catalog(session: Session, case_id: str) -> None:
    """Serialize paragraph-reference validation and position assignment per matter."""
    _transaction_advisory_lock(session, chain="report_section_catalog", scope=case_id)


def _lock_packet_artifact_chain(session: Session, case_id: str) -> None:
    _transaction_advisory_lock(session, chain="packet_artifact", scope=case_id)


def _latest_audit_hash(session: Session, case_id: str) -> str | None:
    return session.execute(
        select(m.AuditEvent.integrity_hash)
        .where(m.AuditEvent.case_id == case_id)
        .order_by(m.AuditEvent.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_account_audit_hash(session: Session) -> str | None:
    return session.execute(
        select(m.AccountAuditEvent.integrity_hash)
        .order_by(m.AccountAuditEvent.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_packet_artifact_hash(session: Session, case_id: str) -> str | None:
    return session.execute(
        select(m.PacketArtifact.integrity_hash)
        .where(m.PacketArtifact.case_id == case_id)
        .order_by(m.PacketArtifact.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_review_decision_hash(session: Session, case_id: str) -> str | None:
    return session.execute(
        select(m.ReviewDecision.integrity_hash)
        .join(m.Conflict, m.Conflict.id == m.ReviewDecision.conflict_id)
        .where(m.Conflict.case_id == case_id)
        .order_by(m.ReviewDecision.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def _lock_claim_rows(session: Session, claim_ids: list[str]) -> dict[str, m.Claim]:
    """Lock affected claims in one global order to prevent cross-conflict cycles."""
    ordered_ids = sorted(set(claim_ids))
    rows = session.execute(
        select(m.Claim)
        .where(m.Claim.id.in_(ordered_ids))
        .order_by(m.Claim.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalars()
    return {row.id: row for row in rows}


def _related_conflicts(session: Session, claim_id: str) -> list[m.Conflict]:
    return list(
        session.execute(
            select(m.Conflict)
            .where(or_(m.Conflict.claim_a_id == claim_id, m.Conflict.claim_b_id == claim_id))
            .order_by(m.Conflict.created_at, m.Conflict.id)
        ).scalars()
    )


def _recompute_eligibility(session: Session, claim: m.Claim) -> None:
    related = _related_conflicts(session, claim.id)
    claim.report_eligibility = compute_claim_eligibility(
        claim.status,
        claim.privilege_status,
        [c.status for c in related],
        # Every decision path lands here. Omitting the quote state would let a
        # reviewer resolve a conflict in favour of a claim whose quote nothing
        # ever confirmed and have it come out report-eligible.
        claim.quote_verification,
    )


def _recompute_claim_status(session: Session, claim: m.Claim) -> None:
    """Re-derive a claim's global status from every conflict it is in, then its
    eligibility. A claim in several conflicts takes the most-decisive of its
    per-conflict dispositions — earlier adjudications are never erased by a
    later one. Callers must have flushed any conflict change first."""
    dispositions = [
        (c.claim_a_disposition if claim.id == c.claim_a_id else c.claim_b_disposition)
        for c in _related_conflicts(session, claim.id)
    ]
    claim.status = decision_rules.aggregate_claim_status(dispositions)
    _recompute_eligibility(session, claim)


def _cited_view(session: Session, claim: m.Claim) -> report_rules.CitedClaim:
    related = _related_conflicts(session, claim.id)
    return report_rules.CitedClaim(
        id=claim.id,
        status=claim.status,
        report_eligibility=claim.report_eligibility,
        unresolved_conflict_summaries=tuple(
            c.summary for c in related if c.status == "unresolved"
        ),
    )


def _section_impact(
    session: Session, paragraph_ref: str, claim_ids: list[str]
) -> report_rules.SectionImpact:
    cited = [
        _cited_view(session, claim)
        for claim_id in claim_ids
        if (claim := session.get(m.Claim, claim_id)) is not None
    ]
    return report_rules.compute_section_impact(paragraph_ref, cited)


def _append_audit(
    session: Session,
    *,
    case_id: str,
    at: datetime,
    actor: m.Reviewer,
    action: str,
    subject_type: str,
    subject_id: str,
    detail: str,
    previous_status: str | None = None,
    new_status: str | None = None,
    report_eligibility_changed: bool = False,
) -> m.AuditEvent:
    # CaseMember.role is the actor's authority inside this matter. The global
    # Reviewer.role belongs only to reviewer-directory/account administration
    # and must never be stamped onto case evidence.
    actor_membership = authorize_case_action(session, actor, case_id)
    _lock_audit_chain(session, case_id)
    previous_hash = _latest_audit_hash(session, case_id)
    event = m.AuditEvent(
        id=_event_id(),
        case_id=case_id,
        at=at,
        actor_reviewer_id=actor.id,
        actor=actor.name,
        actor_role=actor_membership.role,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        previous_status=previous_status,
        new_status=new_status,
        detail=detail,
        report_eligibility_changed=report_eligibility_changed,
        previous_integrity_hash=previous_hash,
        integrity_hash="",
    )
    event.integrity_hash = chain_sha256(
        previous_hash=previous_hash,
        content=audit_event_content(event),
    )
    session.add(event)
    session.flush()
    return event


def _check_expected_version(actual: int, expected: int | None, subject: str) -> None:
    if expected is None:
        raise t.ValidationFailure(
            f"expectedVersion is required when changing an existing {subject.lower()}."
        )
    if actual != expected:
        raise t.StaleUpdate(
            f"{subject} has changed since it was loaded (expected version "
            f"{expected}, current version {actual})."
        )


def _append_account_audit(
    session: Session,
    *,
    at: datetime,
    actor: m.Reviewer,
    subject: m.Reviewer,
    action: str,
    detail: str,
    previous_status: str | None = None,
    new_status: str | None = None,
) -> m.AccountAuditEvent:
    _lock_account_audit_chain(session)
    previous_hash = _latest_account_audit_hash(session)
    event = m.AccountAuditEvent(
        id=_event_id(),
        at=at,
        actor_reviewer_id=actor.id,
        actor=actor.name,
        # Account governance is the global plane, so Reviewer.role is
        # intentional here (unlike every case evidentiary actor role).
        actor_role=actor.role,
        action=action,
        subject_reviewer_id=subject.id,
        previous_status=previous_status,
        new_status=new_status,
        detail=detail,
        previous_integrity_hash=previous_hash,
        integrity_hash="",
    )
    event.integrity_hash = chain_sha256(
        previous_hash=previous_hash,
        content=account_audit_event_content(event),
    )
    session.add(event)
    session.flush()
    return event


# ── Serializers (camelCase, matching src/domain/types.ts) ────────────────


def serialize_claim(
    session: Session,
    claim: m.Claim,
    *,
    visible_conflict_ids: set[str] | None = None,
) -> dict:
    related_conflict_ids = [c.id for c in _related_conflicts(session, claim.id)]
    if visible_conflict_ids is not None:
        related_conflict_ids = [
            conflict_id
            for conflict_id in related_conflict_ids
            if conflict_id in visible_conflict_ids
        ]
    return {
        "id": claim.id,
        "caseId": claim.case_id,
        "text": claim.text,
        "sourceDocumentId": claim.source_document_id,
        "pageRef": claim.page_ref,
        "quote": claim.quote,
        "confidence": claim.confidence,
        "evidenceQuality": claim.evidence_quality,
        "status": claim.status,
        "privilegeStatus": claim.privilege_status,
        "reportEligibility": claim.report_eligibility,
        "reviewer": claim.reviewer,
        "updatedAt": iso_z(claim.updated_at),
        "relatedConflictIds": related_conflict_ids,
        "quoteVerification": claim.quote_verification,
        "sourcePageExtractionId": claim.source_page_extraction_id,
    }


def serialize_conflict(session: Session, conflict: m.Conflict) -> dict:
    decision_ids = list(
        session.execute(
            select(m.ReviewDecision.id)
            .where(m.ReviewDecision.conflict_id == conflict.id)
            .order_by(m.ReviewDecision.created_at, m.ReviewDecision.id)
        ).scalars()
    )
    return {
        "id": conflict.id,
        "caseId": conflict.case_id,
        "claimAId": conflict.claim_a_id,
        "claimBId": conflict.claim_b_id,
        "conflictType": conflict.conflict_type,
        "severity": conflict.severity,
        "status": conflict.status,
        "summary": conflict.summary,
        "createdAt": iso_z(conflict.created_at),
        "decisionIds": decision_ids,
        "claimADisposition": conflict.claim_a_disposition,
        "claimBDisposition": conflict.claim_b_disposition,
        "version": conflict.version,
    }


def serialize_decision(decision: m.ReviewDecision) -> dict:
    return {
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


def serialize_audit_event(event: m.AuditEvent) -> dict:
    return {
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


def serialize_source(source: m.SourceDocument, file_summary: dict | None = None) -> dict:
    payload = {
        "id": source.id,
        "caseId": source.case_id,
        "title": source.title,
        "type": source.type,
        "origin": source.origin,
        "custodian": source.custodian,
        "docketRef": source.docket_ref,
        "ingestedAt": iso_z(source.ingested_at),
        "sha256": source.sha256,
        "privilegeStatus": source.privilege_status,
        "custody": source.custody,
        "isIngested": source.active_extraction_run_id is not None,
    }
    if file_summary is not None:
        # contentSha256 is computed from the stored bytes. source.sha256 is a
        # legacy display field that predates uploads and is unverifiable for
        # seeded rows, so it is never presented as the authoritative hash.
        payload.update(
            {
                "contentSha256": file_summary["contentSha256"],
                "pageCount": file_summary["pageCount"],
                "originalFilename": file_summary["originalFilename"],
                "sizeBytes": file_summary["sizeBytes"],
            }
        )
    return payload


def source_file_summary(session: Session, source_id: str) -> dict | None:
    """Scalar columns only — never loads the document bytes."""
    row = session.execute(
        select(
            m.SourceDocumentFile.content_sha256,
            m.SourceDocumentFile.page_count,
            m.SourceDocumentFile.original_filename,
            m.SourceDocumentFile.size_bytes,
        ).where(m.SourceDocumentFile.source_document_id == source_id)
    ).first()
    if row is None:
        return None
    return {
        "contentSha256": row[0],
        "pageCount": row[1],
        "originalFilename": row[2],
        "sizeBytes": row[3],
    }


def serialize_source_page(page: m.SourcePageExtraction) -> dict:
    return {
        "id": page.id,
        "pageNumber": page.page_number,
        "extractionMethod": page.extraction_method,
        "text": page.extracted_text,
        "textTruncated": page.text_truncated,
        # Percent for display; basis points are the stored, hashable form.
        "ocrConfidence": (
            round(page.ocr_confidence_bps / 100) if page.ocr_confidence_bps is not None else None
        ),
        "failureCode": page.failure_code,
        "failureDetail": page.failure_detail,
        "defaultPageRef": source_rules.default_page_ref(page.page_number),
    }


def serialize_section(session: Session, section: m.ReportSection) -> dict:
    revision = (
        session.get(m.ReportSectionRevision, section.active_revision_id)
        if section.active_revision_id is not None
        else None
    )
    return {
        "id": section.id,
        "caseId": section.case_id,
        "title": section.title,
        "paragraphRef": section.paragraph_ref,
        "text": section.text,
        "claimIds": list(section.claim_ids),
        "approvalState": revision.approval_state if revision is not None else "draft",
        "activeRevisionId": section.active_revision_id,
        "version": section.version,
    }


def serialize_admin_reviewer(reviewer: m.Reviewer) -> dict:
    return {
        "id": reviewer.id,
        "name": reviewer.name,
        "username": reviewer.username,
        "role": reviewer.role,
        "isActive": reviewer.is_active,
        "mfaEnabled": auth.mfa_enabled(reviewer),
        "mustChangePassword": reviewer.must_change_password,
        "passwordChangedAt": iso_z(reviewer.password_changed_at)
        if reviewer.password_changed_at is not None
        else None,
    }


def serialize_account_audit_event(event: m.AccountAuditEvent) -> dict:
    return {
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


def serialize_case_file(case: m.CaseFile) -> dict:
    return {
        "id": case.id,
        "name": case.name,
        "aircraft": case.aircraft,
        "accidentDate": case.accident_date,
        "location": case.location,
        "matterType": case.matter_type,
        "status": case.status,
        "docketRef": case.docket_ref,
    }


def serialize_case_summary(case: m.CaseFile, member: m.CaseMember) -> dict:
    return {
        "caseFile": serialize_case_file(case),
        "caseMembership": serialize_case_member(member),
    }


def _render_packet_pdf(
    packet,
    document: str,
    body_sha256: str,
    *,
    generated_at: datetime,
) -> tuple[bytes | None, str | None]:
    """Render the packet's paginated PDF, or explain why there isn't one.

    Two failure modes, treated differently on purpose.

    A **document that will not render** — too large, or a renderer error on
    this specific content — fails the whole generation. Handing back a packet
    that silently lacks its production artifact would leave the reviewer
    believing they had something producible, and they would find out at
    disclosure. That is the worst possible moment.

    A **runtime with no PDF support at all** degrades instead: the packet is
    still generated, the artifact records that it has no PDF, and the audit
    event says so. This path exists for development and is closed off in
    production by ``production_config_errors``, which refuses to boot a
    production deployment that cannot render — so the capability can never be
    missing silently where it matters.
    """
    from .packet_pdf import (
        PdfRenderingUnavailable,
        PdfTooLarge,
        pdf_sha256,
        render_packet_pdf,
    )

    try:
        pdf = render_packet_pdf(
            packet,
            document,
            body_sha256,
            generated_at=generated_at,
            max_bytes=max_packet_pdf_bytes(),
        )
    except PdfRenderingUnavailable:
        return None, None
    except PdfTooLarge as exc:
        raise t.ValidationFailure(str(exc)) from exc
    return pdf, pdf_sha256(pdf)


def create_matter_op(
    session: Session,
    reviewer: m.Reviewer,
    *,
    body: dict,
) -> dict:
    """Open a new matter with the creating reviewer as its Senior counsel.

    Authorization is on the *global* plane, not a matter role: a matter that
    does not exist yet has no membership to check against. That makes this the
    same authority as reviewer-directory administration, which is why it
    requires a global Senior Aviation Counsel and a verified MFA session (the
    route enforces MFA).

    The rows are created through ``atlas_argus_create_matter`` rather than the
    ORM because row-level security on ``case_file`` and ``case_member`` both
    require an existing active membership — a circular requirement for a new
    matter. See migration 0018 for why that function is safe: it generates the
    id itself, so it cannot be aimed at a matter that already exists.
    """
    allowed, reason = admin_permission(reviewer.role)
    if not allowed:
        raise t.PermissionDenied(reason or "Matter creation requires senior counsel.")

    fields = matter_rules.validate_new_matter(
        name=body.get("name"),
        aircraft=body.get("aircraft"),
        accident_date=body.get("accidentDate"),
        location=body.get("location"),
        matter_type=body.get("matterType"),
        docket_ref=body.get("docketRef"),
    )

    case_id = session.execute(
        text(
            "SELECT atlas_argus_create_matter("
            ":name, :aircraft, :accident_date, :location, "
            ":matter_type, :status, :docket_ref)"
        ),
        {
            "name": fields["name"],
            "aircraft": fields["aircraft"],
            "accident_date": fields["accidentDate"],
            "location": fields["location"],
            "matter_type": fields["matterType"],
            "status": fields["status"],
            "docket_ref": fields["docketRef"],
        },
    ).scalar_one()
    session.flush()

    case = session.get(m.CaseFile, case_id)
    if case is None:  # pragma: no cover - the function raises rather than no-op
        raise t.DomainError("Matter creation did not produce a matter.")
    membership = _case_membership(session, reviewer, case_id)
    if membership is None:  # pragma: no cover - created in the same statement
        raise t.DomainError("Matter creation did not produce a membership.")

    # Appended through the ordinary path so the matter's hash chain is rooted
    # by the same code every other event uses.
    event = _append_audit(
        session,
        case_id=case_id,
        at=_now(),
        actor=reviewer,
        action="matter opened",
        subject_type="reviewer",
        subject_id=reviewer.id,
        new_status="active",
        detail=(
            f"Matter {fields['name']} ({fields['docketRef']}) opened by "
            f"{reviewer.name}, who holds the initial active Senior Aviation "
            f"Counsel membership."
        ),
    )
    return {
        "caseFile": serialize_case_file(case),
        "caseMembership": serialize_case_member(membership),
        "auditEvent": serialize_audit_event(event),
    }


def serialize_case_member_admin(member: m.CaseMember, reviewer: m.Reviewer) -> dict:
    return {
        "caseId": member.case_id,
        "reviewerId": member.reviewer_id,
        "reviewerName": reviewer.name,
        "username": reviewer.username,
        "reviewerRole": reviewer.role,
        "role": member.role,
        "isActive": member.is_active,
        "reviewerIsActive": reviewer.is_active,
    }


def _validate_reviewer_identity(name: str, username: str, role: str) -> tuple[str, str, str]:
    clean_name = name.strip()
    clean_username = username.strip().lower()
    if len(clean_name) < 2:
        raise t.ValidationFailure("Reviewer name is required.")
    if len(clean_username) < 3:
        raise t.ValidationFailure("Username must be at least 3 characters.")
    if not all(ch.isalnum() or ch in "._-" for ch in clean_username):
        raise t.ValidationFailure(
            "Username may contain only letters, numbers, dots, hyphens, and underscores."
        )
    if role not in t.REVIEWER_ROLES:
        raise t.ValidationFailure(f"Unknown reviewer role: {role}.")
    return clean_name, clean_username, role


def _validate_admin_password(new_password: str) -> None:
    if len(new_password) < auth.MIN_PASSWORD_LENGTH:
        raise t.ValidationFailure(
            f"Password must be at least {auth.MIN_PASSWORD_LENGTH} characters."
        )


def _revoke_reviewer_sessions(session: Session, reviewer_id: str) -> None:
    session.execute(
        update(m.AuthSession)
        .where(
            m.AuthSession.reviewer_id == reviewer_id,
            m.AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )


def _has_privilege_clearance(matter_role: str) -> bool:
    return matter_role == "Senior Aviation Counsel"


def _is_withholding_privilege(privilege_status: str) -> bool:
    return privilege_status in t.WITHHOLDING_PRIVILEGE


def _claim_visible_to(matter_role: str, claim: m.Claim) -> bool:
    return _has_privilege_clearance(matter_role) or not _is_withholding_privilege(
        claim.privilege_status
    )


def _source_visible_to(matter_role: str, source: m.SourceDocument) -> bool:
    return _has_privilege_clearance(matter_role) or not _is_withholding_privilege(
        source.privilege_status
    )


def serialize_case_member(member: m.CaseMember) -> dict:
    return {
        "caseId": member.case_id,
        "reviewerId": member.reviewer_id,
        "role": member.role,
        "isActive": member.is_active,
    }


def _case_membership(
    session: Session, reviewer: m.Reviewer, case_id: str
) -> m.CaseMember | None:
    return session.execute(
        select(m.CaseMember).where(
            m.CaseMember.case_id == case_id,
            m.CaseMember.reviewer_id == reviewer.id,
        )
    ).scalar_one_or_none()


def authorize_case_action(session: Session, reviewer: m.Reviewer, case_id: str) -> m.CaseMember:
    """Return the active matter membership that authorizes case work.

    ``Reviewer.role`` is deliberately not consulted here. It governs the
    global reviewer directory/account plane; ``CaseMember.role`` governs
    evidence visibility and legal acts inside one matter.
    """
    membership = _case_membership(session, reviewer, case_id)
    if membership is None or not membership.is_active:
        raise t.PermissionDenied("Reviewer is not an active member of this case.")
    return membership


def _default_case_for_reviewer(
    session: Session, reviewer: m.Reviewer
) -> tuple[m.CaseFile, m.CaseMember]:
    row = session.execute(
        select(m.CaseFile, m.CaseMember)
        .join(m.CaseMember, m.CaseMember.case_id == m.CaseFile.id)
        .where(
            m.CaseMember.reviewer_id == reviewer.id,
            m.CaseMember.is_active.is_(True),
        )
        .order_by(m.CaseFile.id)
        .limit(1)
    ).first()
    if row is None:
        raise t.PermissionDenied("Reviewer is not an active member of any case.")
    return row[0], row[1]


def _case_for_reviewer(
    session: Session, reviewer: m.Reviewer, case_id: str
) -> tuple[m.CaseFile, m.CaseMember]:
    case = session.get(m.CaseFile, case_id)
    if case is None:
        raise t.NotFound("Case not found.")
    return case, authorize_case_action(session, reviewer, case.id)


def _selected_case_for_reviewer(
    session: Session, reviewer: m.Reviewer, case_id: str | None
) -> tuple[m.CaseFile, m.CaseMember]:
    if case_id is None:
        return _default_case_for_reviewer(session, reviewer)
    return _case_for_reviewer(session, reviewer, case_id)


def _require_claim_visible(session: Session, reviewer: m.Reviewer, claim_id: str) -> m.Claim:
    claim = session.get(m.Claim, claim_id)
    if claim is None:
        raise t.NotFound("Claim not found.")
    # Case membership must be checked before privilege visibility: a claim
    # from a case the reviewer isn't on must fail the same way regardless of
    # its privilege status, or the distinct error messages become a cross-case
    # oracle for which claims are privilege-withheld.
    membership = authorize_case_action(session, reviewer, claim.case_id)
    if not _claim_visible_to(membership.role, claim):
        raise t.PermissionDenied("Privilege clearance is required for this evidence.")
    return claim


def _append_section_revision(
    session: Session,
    *,
    section: m.ReportSection,
    reviewer: m.Reviewer,
    at: datetime,
    reason: str,
    approval_state: str = "draft",
) -> m.ReportSectionRevision:
    # Existing-section callers also hold the section row FOR UPDATE. The
    # advisory lock covers initial inserts and makes the chain's serialization
    # rule explicit without relying on append-table lock privileges.
    author_membership = authorize_case_action(session, reviewer, section.case_id)
    _lock_report_revision_chain(session, section.id)
    parent = (
        session.get(m.ReportSectionRevision, section.active_revision_id)
        if section.active_revision_id is not None
        else None
    )
    previous_hash = parent.integrity_hash if parent is not None else None
    revision = m.ReportSectionRevision(
        id=_revision_id(),
        section_id=section.id,
        case_id=section.case_id,
        title=section.title,
        paragraph_ref=section.paragraph_ref,
        text=section.text,
        claim_ids=list(section.claim_ids),
        author_reviewer_id=reviewer.id,
        author=reviewer.name,
        author_role=author_membership.role,
        created_at=at,
        revision_reason=reason,
        parent_revision_id=section.active_revision_id,
        approval_state=approval_state,
        content_sha256=revision_content_sha256(
            case_id=section.case_id,
            section_id=section.id,
            title=section.title,
            paragraph_ref=section.paragraph_ref,
            text=section.text,
            claim_ids=list(section.claim_ids),
        ),
        previous_integrity_hash=previous_hash,
        integrity_hash="",
    )
    revision.integrity_hash = chain_sha256(
        previous_hash=previous_hash,
        content=report_section_revision_content(revision),
    )
    session.add(revision)
    session.flush()
    section.active_revision_id = revision.id
    session.flush()
    return revision


def _verify_chain(rows: list, content_fn, chain_name: str) -> dict:
    issues: list[dict] = []
    previous_hash: str | None = None
    for row in rows:
        if row.previous_integrity_hash != previous_hash:
            issues.append(
                {
                    "id": row.id,
                    "kind": "previous_hash_mismatch",
                    "expected": previous_hash,
                    "actual": row.previous_integrity_hash,
                }
            )
        expected_hash = chain_sha256(
            previous_hash=previous_hash,
            content=content_fn(row),
        )
        if row.integrity_hash != expected_hash:
            issues.append(
                {
                    "id": row.id,
                    "kind": "integrity_hash_mismatch",
                    "expected": expected_hash,
                    "actual": row.integrity_hash,
                }
            )
        previous_hash = row.integrity_hash
    return {
        "chain": chain_name,
        "ok": len(issues) == 0,
        "checked": len(rows),
        "rootIntegrityHash": previous_hash,
        "issues": issues,
    }


def _packet_artifact_issues(artifact: m.PacketArtifact) -> list[dict]:
    issues: list[dict] = []
    manifest = dict(artifact.manifest)
    document_sha256 = hashlib.sha256(artifact.document.encode("utf-8")).hexdigest()
    if manifest.get("bodySha256") != artifact.body_sha256:
        issues.append(
            {
                "id": artifact.id,
                "kind": "packet_body_hash_mismatch",
                "expected": artifact.body_sha256,
                "actual": manifest.get("bodySha256"),
            }
        )
    if manifest.get("documentSha256") != document_sha256:
        issues.append(
            {
                "id": artifact.id,
                "kind": "packet_document_hash_mismatch",
                "expected": document_sha256,
                "actual": manifest.get("documentSha256"),
            }
        )
    manifest_sha256 = packet_artifact_manifest_hash(manifest)
    if manifest.get("manifestSha256") != manifest_sha256:
        issues.append(
            {
                "id": artifact.id,
                "kind": "packet_manifest_hash_mismatch",
                "expected": manifest_sha256,
                "actual": manifest.get("manifestSha256"),
            }
        )
    artifact_sha256 = canonical_sha256(
        {
            "schema": "atlas_argus.packet_artifact.v1",
            "documentSha256": document_sha256,
            "manifestSha256": manifest_sha256,
        }
    )
    if manifest.get("artifactSha256") != artifact_sha256:
        issues.append(
            {
                "id": artifact.id,
                "kind": "packet_artifact_hash_mismatch",
                "expected": artifact_sha256,
                "actual": manifest.get("artifactSha256"),
            }
        )
    if (
        "previousPacketIntegrityHash" in manifest
        and manifest["previousPacketIntegrityHash"] != artifact.previous_integrity_hash
    ):
        issues.append(
            {
                "id": artifact.id,
                "kind": "packet_previous_hash_manifest_mismatch",
                "expected": artifact.previous_integrity_hash,
                "actual": manifest["previousPacketIntegrityHash"],
            }
        )
    return issues


def _verify_packet_artifact_chain(rows: list[m.PacketArtifact]) -> dict:
    result = _verify_chain(rows, packet_artifact_content, "packet_artifact")
    packet_issues: list[dict] = []
    for row in rows:
        packet_issues.extend(_packet_artifact_issues(row))
    result["issues"] = [*result["issues"], *packet_issues]
    result["ok"] = len(result["issues"]) == 0
    return result


def _verify_section_revision_chain(rows: list[m.ReportSectionRevision]) -> dict:
    # Each section has its own independent lineage (parent_revision_id), not
    # one flat sequence across the case — verify each section's chain on its
    # own rather than treating unrelated sections' revisions as one chain.
    by_section: dict[str, list[m.ReportSectionRevision]] = {}
    for row in rows:
        by_section.setdefault(row.section_id, []).append(row)
    issues: list[dict] = []
    checked = 0
    for section_rows in by_section.values():
        by_id = {row.id: row for row in section_rows}
        roots = [row for row in section_rows if row.parent_revision_id is None]
        if len(roots) != 1:
            issues.append(
                {
                    "id": section_rows[0].section_id,
                    "kind": "revision_lineage_root_count",
                    "expected": 1,
                    "actual": len(roots),
                }
            )
        children_by_parent: dict[str, list[str]] = {}
        for row in section_rows:
            if row.parent_revision_id is not None:
                children_by_parent.setdefault(row.parent_revision_id, []).append(row.id)
        for parent_id, child_ids in children_by_parent.items():
            if len(child_ids) > 1:
                issues.append(
                    {
                        "id": parent_id,
                        "kind": "revision_lineage_fork",
                        "expected": 1,
                        "actual": len(child_ids),
                    }
                )

        for row in section_rows:
            expected_content_hash = revision_content_sha256(
                case_id=row.case_id,
                section_id=row.section_id,
                title=row.title,
                paragraph_ref=row.paragraph_ref,
                text=row.text,
                claim_ids=list(row.claim_ids),
            )
            if row.content_sha256 != expected_content_hash:
                issues.append(
                    {
                        "id": row.id,
                        "kind": "revision_content_hash_mismatch",
                        "expected": expected_content_hash,
                        "actual": row.content_sha256,
                    }
                )

            parent = (
                by_id.get(row.parent_revision_id)
                if row.parent_revision_id is not None
                else None
            )
            if row.parent_revision_id is not None and parent is None:
                issues.append(
                    {
                        "id": row.id,
                        "kind": "revision_parent_missing_or_cross_section",
                        "expected": row.parent_revision_id,
                        "actual": None,
                    }
                )
            expected_previous_hash = parent.integrity_hash if parent is not None else None
            if row.previous_integrity_hash != expected_previous_hash:
                issues.append(
                    {
                        "id": row.id,
                        "kind": "previous_hash_mismatch",
                        "expected": expected_previous_hash,
                        "actual": row.previous_integrity_hash,
                    }
                )
            expected_integrity_hash = chain_sha256(
                previous_hash=expected_previous_hash,
                content=report_section_revision_content(row),
            )
            if row.integrity_hash != expected_integrity_hash:
                issues.append(
                    {
                        "id": row.id,
                        "kind": "integrity_hash_mismatch",
                        "expected": expected_integrity_hash,
                        "actual": row.integrity_hash,
                    }
                )
            checked += 1
    return {
        "chain": "report_section_revision",
        "ok": len(issues) == 0,
        "checked": checked,
        "issues": issues,
    }


def verify_case_integrity(
    session: Session, reviewer: m.Reviewer, *, case_id: str | None = None
) -> dict:
    case, _membership = _selected_case_for_reviewer(session, reviewer, case_id)
    audit_rows = list(
        session.execute(
            select(m.AuditEvent)
            .where(m.AuditEvent.case_id == case.id)
            .order_by(m.AuditEvent.seq)
        ).scalars()
    )
    packet_rows = list(
        session.execute(
            select(m.PacketArtifact)
            .where(m.PacketArtifact.case_id == case.id)
            .order_by(m.PacketArtifact.seq)
        ).scalars()
    )
    section_revision_rows = list(
        session.execute(
            select(m.ReportSectionRevision)
            .where(m.ReportSectionRevision.case_id == case.id)
            .order_by(m.ReportSectionRevision.section_id, m.ReportSectionRevision.created_at)
        ).scalars()
    )
    decision_rows = list(
        session.execute(
            select(m.ReviewDecision)
            .join(m.Conflict, m.Conflict.id == m.ReviewDecision.conflict_id)
            .where(m.Conflict.case_id == case.id)
            .order_by(m.ReviewDecision.seq)
        ).scalars()
    )
    audit = _verify_chain(audit_rows, audit_event_content, "audit_event")
    packets = _verify_packet_artifact_chain(packet_rows)
    sections = _verify_section_revision_chain(section_revision_rows)
    decisions = _verify_chain(decision_rows, review_decision_content, "review_decision")
    extractions = _verify_extraction_integrity(session, case.id)
    return {
        "ok": (
            audit["ok"]
            and packets["ok"]
            and sections["ok"]
            and decisions["ok"]
            and extractions["ok"]
        ),
        "caseId": case.id,
        "audit": audit,
        "packets": packets,
        "sections": sections,
        "decisions": decisions,
        "extractions": extractions,
    }


def _verify_extraction_integrity(session: Session, case_id: str) -> dict:
    """Recompute what ingestion recorded, from the stored bytes and text.

    Checked in dependency order, because a later hash is only meaningful if the
    earlier one held: the document's own bytes, then each page's text, then the
    manifest that binds the pages to the run. All three are anchored in the
    case audit chain at ingestion, so a rewrite has to defeat this *and* the
    chain to go unnoticed.
    """
    runs = list(
        session.execute(
            select(m.SourceExtractionRun)
            .where(m.SourceExtractionRun.case_id == case_id)
            .order_by(m.SourceExtractionRun.id)
        ).scalars()
    )
    issues: list[dict] = []
    checked_documents: set[str] = set()

    for run in runs:
        pages = list(
            session.execute(
                select(m.SourcePageExtraction)
                .where(m.SourcePageExtraction.extraction_run_id == run.id)
                .order_by(m.SourcePageExtraction.page_number)
            ).scalars()
        )

        if run.source_document_id not in checked_documents:
            checked_documents.add(run.source_document_id)
            stored = session.execute(
                select(
                    m.SourceDocumentFile.content, m.SourceDocumentFile.content_sha256
                ).where(
                    m.SourceDocumentFile.source_document_id == run.source_document_id
                )
            ).first()
            if stored is None:
                issues.append(
                    {
                        "id": run.source_document_id,
                        "kind": "source_document_file",
                        "issue": "stored document bytes are missing",
                    }
                )
            elif hashlib.sha256(stored[0]).hexdigest() != stored[1]:
                issues.append(
                    {
                        "id": run.source_document_id,
                        "kind": "source_document_file",
                        "issue": "stored document does not match its recorded hash",
                    }
                )

        for page in pages:
            recomputed = hashlib.sha256(page.extracted_text.encode("utf-8")).hexdigest()
            if recomputed != page.text_sha256:
                issues.append(
                    {
                        "id": page.id,
                        "kind": "source_page_extraction",
                        "issue": (
                            f"extracted text for page {page.page_number} does not match "
                            f"its recorded hash"
                        ),
                    }
                )

        expected_manifest = extraction_manifest_sha256(
            content_sha256=session.execute(
                select(m.SourceDocumentFile.content_sha256).where(
                    m.SourceDocumentFile.source_document_id == run.source_document_id
                )
            ).scalar_one_or_none()
            or "",
            source_document_id=run.source_document_id,
            run=run,
            pages=pages,
        )
        if expected_manifest != run.manifest_sha256:
            issues.append(
                {
                    "id": run.id,
                    "kind": "source_extraction_run",
                    "issue": "extraction manifest does not match its recorded hash",
                }
            )

    return {"ok": not issues, "runs": len(runs), "issues": issues}


def verify_packet_integrity(
    session: Session,
    reviewer: m.Reviewer,
    *,
    packet_id: str,
    case_id: str | None = None,
) -> dict:
    artifact = session.get(m.PacketArtifact, packet_id)
    if artifact is None:
        raise t.NotFound("Packet artifact not found.")
    if case_id is not None and artifact.case_id != case_id:
        raise t.NotFound("Packet artifact not found.")
    _case_for_reviewer(session, reviewer, artifact.case_id)
    rows = list(
        session.execute(
            select(m.PacketArtifact)
            .where(m.PacketArtifact.case_id == artifact.case_id)
            .order_by(m.PacketArtifact.seq)
        ).scalars()
    )
    chain = _verify_packet_artifact_chain(rows)
    packet_issues = [issue for issue in chain["issues"] if issue["id"] == packet_id]

    # Recompute from the stored bytes rather than re-rendering. Re-rendering
    # would compare against whatever the currently installed WeasyPrint, font
    # and Pango versions produce today, which is not what was disclosed; the
    # question this answers is whether the artifact still is what it was.
    pdf_issue = _verify_stored_pdf(session, artifact)
    if pdf_issue is not None:
        packet_issues = [*packet_issues, pdf_issue]

    return {
        "ok": chain["ok"] and pdf_issue is None,
        "caseId": artifact.case_id,
        "packetId": artifact.id,
        "packetType": artifact.packet_type,
        "generatedAt": iso_z(artifact.generated_at),
        "bodySha256": artifact.body_sha256,
        "artifactSha256": artifact.manifest.get("artifactSha256"),
        "pdfSha256": artifact.pdf_sha256,
        "hasPdf": artifact.pdf_sha256 is not None,
        "packetIntegrityHash": artifact.integrity_hash,
        "packetIssues": packet_issues,
        "chain": chain,
    }


def _verify_stored_pdf(session: Session, artifact: m.PacketArtifact) -> dict | None:
    """Confirm the stored PDF still hashes to what was recorded."""
    if artifact.pdf_sha256 is None:
        return None
    stored = session.execute(
        select(m.PacketArtifact.pdf).where(m.PacketArtifact.id == artifact.id)
    ).scalar_one_or_none()
    if stored is None:
        return {
            "id": artifact.id,
            "kind": "packet_pdf",
            "issue": "the recorded PDF is missing",
        }
    if hashlib.sha256(stored).hexdigest() != artifact.pdf_sha256:
        return {
            "id": artifact.id,
            "kind": "packet_pdf",
            "issue": "the stored PDF does not match its recorded hash",
        }
    return None


def _packet_issue_ids(verification: dict) -> set[str]:
    return {
        issue["id"]
        for issue in verification["issues"]
        if isinstance(issue, dict) and isinstance(issue.get("id"), str)
    }


def serialize_packet_artifact_summary(
    artifact: m.PacketArtifact,
    *,
    integrity_ok: bool,
    reveal_generator: bool = True,
) -> dict:
    manifest = dict(artifact.manifest)
    return {
        "packetId": artifact.id,
        "caseId": artifact.case_id,
        "packetType": artifact.packet_type,
        "generatedAt": iso_z(artifact.generated_at),
        "generatedByReviewerId": (
            artifact.generated_by_reviewer_id if reveal_generator else None
        ),
        "generatedByName": artifact.generated_by_name if reveal_generator else None,
        "generatedByRole": artifact.generated_by_role if reveal_generator else None,
        "filename": artifact.filename,
        "bodySha256": artifact.body_sha256,
        "documentSha256": manifest.get("documentSha256"),
        "manifestSha256": manifest.get("manifestSha256"),
        "artifactSha256": manifest.get("artifactSha256"),
        "pdfSha256": artifact.pdf_sha256,
        #: Artifacts generated before PDF export exist and are valid; they
        #: simply have no PDF, and the UI must say so rather than offering a
        #: download that cannot work.
        "hasPdf": artifact.pdf_sha256 is not None,
        "pdfFilename": (
            artifact.filename.rsplit(".", 1)[0] + ".pdf"
            if artifact.pdf_sha256 is not None
            else None
        ),
        "packetIntegrityHash": artifact.integrity_hash,
        "stats": {
            "included": sum(
                1
                for entry in manifest.get("entries", [])
                if entry.get("disposition") == "included"
            ),
            "excluded": sum(
                1
                for entry in manifest.get("entries", [])
                if entry.get("disposition") == "excluded"
            ),
            "withheld": sum(
                1
                for entry in manifest.get("entries", [])
                if entry.get("disposition") == "withheld"
            ),
        },
        "integrityOk": integrity_ok,
    }


def list_packet_artifacts(
    session: Session,
    reviewer: m.Reviewer,
    *,
    case_id: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    case, membership = _selected_case_for_reviewer(session, reviewer, case_id)
    reveal_generator = _has_privilege_clearance(membership.role)
    total = session.execute(
        select(func.count())
        .select_from(m.PacketArtifact)
        .where(m.PacketArtifact.case_id == case.id)
    ).scalar_one()
    all_rows = list(
        session.execute(
            select(m.PacketArtifact)
            .where(m.PacketArtifact.case_id == case.id)
            .order_by(m.PacketArtifact.seq)
        ).scalars()
    )
    verification = _verify_packet_artifact_chain(all_rows)
    issue_ids = _packet_issue_ids(verification)
    page = list(
        session.execute(
            select(m.PacketArtifact)
            .where(m.PacketArtifact.case_id == case.id)
            .order_by(m.PacketArtifact.seq.desc())
            .offset(offset)
            .limit(limit)
        ).scalars()
    )
    return {
        "caseId": case.id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "verification": {
            "ok": verification["ok"],
            "rootIntegrityHash": verification["rootIntegrityHash"],
            "checked": verification["checked"],
        },
        "packets": [
            serialize_packet_artifact_summary(
                row,
                integrity_ok=row.id not in issue_ids,
                reveal_generator=reveal_generator,
            )
            for row in page
        ],
    }


def get_packet_pdf(
    session: Session,
    reviewer: m.Reviewer,
    *,
    packet_id: str,
    case_id: str | None = None,
) -> tuple[bytes, str]:
    """Return the stored PDF bytes and a safe download filename.

    Applies exactly the same access rules as reading the artifact back — an
    internal packet still needs privilege clearance — and additionally refuses
    to serve a PDF whose integrity check fails. Handing over a document that
    the system itself cannot vouch for is worse than handing over nothing,
    because the recipient has no way to know.
    """
    from .packet_pdf import content_disposition_filename

    artifact = session.get(m.PacketArtifact, packet_id)
    if artifact is None:
        raise t.NotFound("Packet artifact not found.")
    if case_id is not None and artifact.case_id != case_id:
        raise t.NotFound("Packet artifact not found.")
    _case, membership = _case_for_reviewer(session, reviewer, artifact.case_id)
    if artifact.packet_type == "internal" and not _has_privilege_clearance(membership.role):
        raise t.PermissionDenied("Privilege clearance is required for this evidence.")

    if artifact.pdf_sha256 is None:
        raise t.NotFound(
            "This packet has no PDF. It was generated before controlled PDF "
            "export existed; regenerate the packet to produce one."
        )

    verification = verify_packet_integrity(
        session, reviewer, packet_id=artifact.id, case_id=artifact.case_id
    )
    if not verification["ok"]:
        raise t.PermissionDenied(
            "This packet failed its integrity check and cannot be downloaded."
        )

    stored = session.execute(
        select(m.PacketArtifact.pdf).where(m.PacketArtifact.id == artifact.id)
    ).scalar_one_or_none()
    if stored is None:  # pragma: no cover - verification above already caught it
        raise t.NotFound("Packet artifact not found.")

    filename = content_disposition_filename(
        artifact.filename.rsplit(".", 1)[0] + ".pdf"
    )
    return bytes(stored), filename


def get_packet_artifact(
    session: Session,
    reviewer: m.Reviewer,
    *,
    packet_id: str,
    case_id: str | None = None,
) -> dict:
    artifact = session.get(m.PacketArtifact, packet_id)
    if artifact is None:
        raise t.NotFound("Packet artifact not found.")
    if case_id is not None and artifact.case_id != case_id:
        raise t.NotFound("Packet artifact not found.")
    _case, membership = _case_for_reviewer(session, reviewer, artifact.case_id)
    # Internal packets deliberately include privileged material unredacted for
    # case-team work product (packet.py's "internal" doctrine) — only
    # privilege-cleared reviewers may read one back. Production packets are
    # already redacted at generation time, so any case member may fetch them.
    if artifact.packet_type == "internal" and not _has_privilege_clearance(membership.role):
        raise t.PermissionDenied("Privilege clearance is required for this evidence.")
    verification = verify_packet_integrity(
        session,
        reviewer,
        packet_id=artifact.id,
        case_id=artifact.case_id,
    )
    return {
        **serialize_packet_artifact_summary(
            artifact,
            integrity_ok=verification["ok"],
            reveal_generator=_has_privilege_clearance(membership.role),
        ),
        "manifest": artifact.manifest,
        "document": artifact.document,
        "verification": verification,
    }


def verify_account_audit_integrity_admin(session: Session) -> dict:
    rows = list(
        session.execute(select(m.AccountAuditEvent).order_by(m.AccountAuditEvent.seq)).scalars()
    )
    result = _verify_chain(
        rows,
        account_audit_event_content,
        "account_audit_event",
    )
    return {"ok": result["ok"], "accountAudit": result}


# ── Operations ───────────────────────────────────────────────────────────


def get_case_state(
    session: Session, reviewer: m.Reviewer, *, case_id: str | None = None
) -> dict:
    case, membership = _selected_case_for_reviewer(session, reviewer, case_id)
    reviewer_rows = session.execute(
        select(m.Reviewer, m.CaseMember)
        .join(m.CaseMember, m.CaseMember.reviewer_id == m.Reviewer.id)
        .where(
            m.CaseMember.case_id == case.id,
            m.CaseMember.is_active.is_(True),
        )
        .order_by(m.Reviewer.id)
    ).all()
    all_sources = list(
        session.execute(
            select(m.SourceDocument)
            .where(m.SourceDocument.case_id == case.id)
            .order_by(m.SourceDocument.id)
        ).scalars()
    )
    sources = [s for s in all_sources if _source_visible_to(membership.role, s)]
    all_claims = list(
        session.execute(
            select(m.Claim).where(m.Claim.case_id == case.id).order_by(m.Claim.id)
        ).scalars()
    )
    claims = [c for c in all_claims if _claim_visible_to(membership.role, c)]
    visible_claim_ids = {c.id for c in claims}
    all_conflicts = list(
        session.execute(
            select(m.Conflict)
            .where(m.Conflict.case_id == case.id)
            .order_by(m.Conflict.created_at, m.Conflict.id)
        ).scalars()
    )
    conflicts = [
        c
        for c in all_conflicts
        if c.claim_a_id in visible_claim_ids and c.claim_b_id in visible_claim_ids
    ]
    visible_conflict_ids = {c.id for c in conflicts}
    claims_with_hidden_conflicts = {
        claim.id
        for claim in claims
        if any(
            conflict.id not in visible_conflict_ids
            for conflict in all_conflicts
            if claim.id in (conflict.claim_a_id, conflict.claim_b_id)
        )
    }
    decisions = list(
        session.execute(
            select(m.ReviewDecision).order_by(m.ReviewDecision.created_at)
        ).scalars()
    )
    decisions = [d for d in decisions if d.conflict_id in visible_conflict_ids]
    audit_events = list(
        session.execute(
            select(m.AuditEvent)
            .where(m.AuditEvent.case_id == case.id)
            .order_by(m.AuditEvent.seq.desc())
        ).scalars()
    )
    visible_source_ids = {s.id for s in sources}
    visible_section_ids: set[str] = set()
    sections = list(
        session.execute(
            select(m.ReportSection)
            .where(m.ReportSection.case_id == case.id)
            .order_by(m.ReportSection.position)
        ).scalars()
    )
    sections = [
        s for s in sections if all(claim_id in visible_claim_ids for claim_id in s.claim_ids)
    ]
    visible_section_ids = {s.id for s in sections}
    sections_with_hidden_history = {
        section_id
        for section_id, claim_ids in session.execute(
            select(m.ReportSectionRevision.section_id, m.ReportSectionRevision.claim_ids).where(
                m.ReportSectionRevision.case_id == case.id,
                m.ReportSectionRevision.section_id.in_(visible_section_ids),
            )
        )
        if any(claim_id not in visible_claim_ids for claim_id in claim_ids)
    }

    def audit_visible(event: m.AuditEvent) -> bool:
        if _has_privilege_clearance(membership.role):
            return True
        if event.subject_type == "claim":
            if event.subject_id not in visible_claim_ids:
                return False
            # A visible public claim can still participate in a conflict whose
            # counterpart is privilege-hidden. Status-event details name that
            # hidden conflict, so retain only the safe extraction history.
            return not (
                event.subject_id in claims_with_hidden_conflicts
                and event.action != "claim extracted"
            )
        if event.subject_type == "conflict":
            return event.subject_id in visible_conflict_ids
        if event.subject_type == "source":
            return event.subject_id in visible_source_ids
        if event.subject_type == "report_section":
            # Current citations alone are insufficient: an older revision (and
            # its audit detail) may name a privileged claim that was later
            # removed. Hide section audit history conservatively while keeping
            # the now-public current section visible.
            return (
                event.subject_id in visible_section_ids
                and event.subject_id not in sections_with_hidden_history
            )
        return False

    audit_events = [e for e in audit_events if audit_visible(e)]

    return {
        "caseFile": serialize_case_file(case),
        "caseMembership": serialize_case_member(membership),
        "reviewers": [
            {"id": r.id, "name": r.name, "role": member.role} for r, member in reviewer_rows
        ],
        "sources": [serialize_source(s) for s in sources],
        "claims": [
            serialize_claim(session, c, visible_conflict_ids=visible_conflict_ids)
            for c in claims
        ],
        "conflicts": [serialize_conflict(session, c) for c in conflicts],
        "conflictOrder": [c.id for c in conflicts],
        "decisions": [serialize_decision(d) for d in decisions],
        "auditEvents": [serialize_audit_event(e) for e in audit_events],
        "reportSections": [serialize_section(session, s) for s in sections],
    }


def list_cases(session: Session, reviewer: m.Reviewer) -> dict:
    rows = session.execute(
        select(m.CaseFile, m.CaseMember)
        .join(m.CaseMember, m.CaseMember.case_id == m.CaseFile.id)
        .where(
            m.CaseMember.reviewer_id == reviewer.id,
            m.CaseMember.is_active.is_(True),
        )
        .order_by(m.CaseFile.name, m.CaseFile.id)
    ).all()
    return {"cases": [serialize_case_summary(case, member) for case, member in rows]}


def list_reviewers_admin(session: Session) -> dict:
    reviewers = session.execute(select(m.Reviewer).order_by(m.Reviewer.name)).scalars()
    return {"reviewers": [serialize_admin_reviewer(r) for r in reviewers]}


def list_account_audit_admin(session: Session) -> dict:
    events = session.execute(
        select(m.AccountAuditEvent).order_by(m.AccountAuditEvent.seq.desc()).limit(200)
    ).scalars()
    return {"events": [serialize_account_audit_event(e) for e in events]}


def list_case_members_admin(session: Session, actor: m.Reviewer, case_id: str) -> dict:
    _case_for_reviewer(session, actor, case_id)
    rows = session.execute(
        select(m.CaseMember, m.Reviewer)
        .join(m.Reviewer, m.Reviewer.id == m.CaseMember.reviewer_id)
        .where(m.CaseMember.case_id == case_id)
        .order_by(m.Reviewer.name, m.Reviewer.id)
    ).all()
    return {
        "members": [serialize_case_member_admin(member, reviewer) for member, reviewer in rows]
    }


def set_case_member_admin(
    session: Session,
    actor: m.Reviewer,
    case_id: str,
    *,
    reviewer_id: str,
    role: str | None = None,
    is_active: bool = True,
) -> dict:
    case, _membership = _case_for_reviewer(session, actor, case_id)
    reviewer = session.get(m.Reviewer, reviewer_id)
    if reviewer is None:
        raise t.NotFound("Reviewer not found.")
    if not reviewer.is_active and is_active:
        raise t.ValidationFailure("Reviewer must be active before joining a case.")
    existing = _case_membership(session, reviewer, case_id)
    # An omitted role must never widen an established matter role. The global
    # Reviewer.role belongs to the account plane; falling back to it here would
    # silently restore full matter authority — including privilege clearance —
    # to a member who was deliberately narrowed on this matter, on the next
    # reactivation. Only the first join has no matter role to preserve.
    clean_role = (role or (existing.role if existing is not None else reviewer.role)).strip()
    if clean_role not in t.REVIEWER_ROLES:
        raise t.ValidationFailure(f"Unknown reviewer role: {clean_role}.")
    previous_status = "inactive"
    action = "case member added"
    now = _now()
    if existing is None:
        member = m.CaseMember(
            id=f"cm-{case_id}-{reviewer.id}",
            case_id=case_id,
            reviewer_id=reviewer.id,
            role=clean_role,
            is_active=is_active,
        )
        session.add(member)
    else:
        previous_status = "active" if existing.is_active else "inactive"
        action = "case member status updated"
        member = existing
        member.role = clean_role
        member.is_active = is_active
    session.flush()
    new_status = "active" if member.is_active else "inactive"
    event = _append_account_audit(
        session,
        at=now,
        actor=actor,
        subject=reviewer,
        action=action,
        previous_status=previous_status,
        new_status=new_status,
        detail=f"{reviewer.name} marked {new_status} on {case.name} as {member.role}.",
    )
    return {
        "member": serialize_case_member_admin(member, reviewer),
        "accountAuditEvent": serialize_account_audit_event(event),
    }


def deactivate_case_member_admin(
    session: Session,
    actor: m.Reviewer,
    case_id: str,
    reviewer_id: str,
) -> dict:
    if actor.id == reviewer_id:
        raise t.ValidationFailure("You cannot remove your own access to this case.")
    _case_for_reviewer(session, actor, case_id)
    reviewer = session.get(m.Reviewer, reviewer_id)
    if reviewer is None:
        raise t.NotFound("Reviewer not found.")
    member = _case_membership(session, reviewer, case_id)
    if member is None:
        raise t.NotFound("Case member not found.")
    return set_case_member_admin(
        session,
        actor,
        case_id,
        reviewer_id=reviewer_id,
        role=member.role,
        is_active=False,
    )


def create_reviewer_admin(
    session: Session,
    actor: m.Reviewer,
    *,
    name: str,
    username: str,
    role: str,
    initial_password: str,
) -> dict:
    clean_name, clean_username, clean_role = _validate_reviewer_identity(name, username, role)
    _validate_admin_password(initial_password)
    existing = session.execute(
        select(m.Reviewer.id).where(m.Reviewer.username == clean_username)
    ).scalar_one_or_none()
    if existing is not None:
        raise t.ValidationFailure("Username is already in use.")

    now = _now()
    reviewer = m.Reviewer(
        id=_reviewer_id(),
        name=clean_name,
        role=clean_role,
        username=clean_username,
        password_hash=auth.hash_password(initial_password),
        must_change_password=True,
        password_changed_at=None,
        is_active=True,
    )
    session.add(reviewer)
    try:
        session.flush()
    except IntegrityError as exc:
        if "reviewer_username_key" in str(exc.orig):
            raise t.ValidationFailure("Username is already in use.") from exc
        raise
    event = _append_account_audit(
        session,
        at=now,
        actor=actor,
        subject=reviewer,
        action="reviewer provisioned",
        new_status="active",
        detail=f"{reviewer.name} ({reviewer.role}) provisioned as {reviewer.username}; password rotation required.",
    )
    return {
        "reviewer": serialize_admin_reviewer(reviewer),
        "accountAuditEvent": serialize_account_audit_event(event),
    }


def set_reviewer_active_admin(
    session: Session,
    actor: m.Reviewer,
    reviewer_id: str,
    *,
    is_active: bool,
) -> dict:
    reviewer = session.get(m.Reviewer, reviewer_id)
    if reviewer is None:
        raise t.NotFound("Reviewer not found.")
    if reviewer.id == actor.id and not is_active:
        raise t.ValidationFailure("You cannot deactivate your own account.")

    previous_status = "active" if reviewer.is_active else "inactive"
    new_status = "active" if is_active else "inactive"
    now = _now()
    reviewer.is_active = is_active
    if not is_active:
        _revoke_reviewer_sessions(session, reviewer.id)
    session.flush()
    event = _append_account_audit(
        session,
        at=now,
        actor=actor,
        subject=reviewer,
        action="reviewer status updated",
        previous_status=previous_status,
        new_status=new_status,
        detail=f"{reviewer.name} marked {new_status}.",
    )
    return {
        "reviewer": serialize_admin_reviewer(reviewer),
        "accountAuditEvent": serialize_account_audit_event(event),
    }


def reset_reviewer_password_admin(
    session: Session,
    actor: m.Reviewer,
    reviewer_id: str,
    *,
    new_password: str,
) -> dict:
    reviewer = session.get(m.Reviewer, reviewer_id)
    if reviewer is None:
        raise t.NotFound("Reviewer not found.")
    _validate_admin_password(new_password)

    now = _now()
    reviewer.password_hash = auth.hash_password(new_password)
    reviewer.must_change_password = True
    reviewer.password_changed_at = None
    _revoke_reviewer_sessions(session, reviewer.id)
    session.flush()
    event = _append_account_audit(
        session,
        at=now,
        actor=actor,
        subject=reviewer,
        action="reviewer password reset",
        detail=f"{reviewer.name} must set a personal password on next sign-in.",
    )
    return {
        "reviewer": serialize_admin_reviewer(reviewer),
        "accountAuditEvent": serialize_account_audit_event(event),
    }


def reset_reviewer_mfa_admin(
    session: Session,
    actor: m.Reviewer,
    reviewer_id: str,
) -> dict:
    if actor.id == reviewer_id:
        raise t.ValidationFailure("You cannot reset your own MFA factor.")
    reviewer = session.get(m.Reviewer, reviewer_id)
    if reviewer is None:
        raise t.NotFound("Reviewer not found.")
    if not auth.mfa_enabled(reviewer):
        raise t.ValidationFailure("Reviewer does not have MFA configured.")

    now = _now()
    reviewer.mfa_secret = None
    reviewer.mfa_enabled_at = None
    _revoke_reviewer_sessions(session, reviewer.id)
    auth.clear_mfa_throttle(session, reviewer.id)
    session.flush()
    event = _append_account_audit(
        session,
        at=now,
        actor=actor,
        subject=reviewer,
        action="reviewer MFA reset",
        previous_status="enabled",
        new_status="not_enrolled",
        detail=(
            f"{reviewer.name}'s MFA factor was reset; all sessions were revoked "
            "and a new factor must be enrolled."
        ),
    )
    return {
        "reviewer": serialize_admin_reviewer(reviewer),
        "accountAuditEvent": serialize_account_audit_event(event),
    }


def _visible_duplicate_source_ids(
    session: Session, *, case_id: str, content_hash: str, matter_role: str
) -> list[str]:
    """Sources in this matter with byte-identical content that this reviewer is
    allowed to know about.

    Advisory only. Identical bytes legitimately enter a matter twice — a
    different custodian, a separate production, the regulator's copy versus
    opposing counsel's — and those are distinct evidentiary records, so this
    never blocks or merges. Privilege-filtered so the count itself cannot
    reveal that a withheld document exists.
    """
    rows = session.execute(
        select(m.SourceDocument)
        .join(
            m.SourceDocumentFile,
            m.SourceDocumentFile.source_document_id == m.SourceDocument.id,
        )
        .where(
            m.SourceDocument.case_id == case_id,
            m.SourceDocumentFile.content_sha256 == content_hash,
        )
        .order_by(m.SourceDocument.id)
    ).scalars()
    return [source.id for source in rows if _source_visible_to(matter_role, source)]


def prepare_source_upload(
    session: Session,
    reviewer: m.Reviewer,
    *,
    case_id: str,
    membership: m.CaseMember,
    body: dict,
) -> tuple[bytes, dict, dict | None]:
    """Phase 1 of an upload: everything that must happen before extraction.

    Returns ``(raw_bytes, prepared, replay)``. A non-None ``replay`` means this
    exact request already succeeded and its stored result should be returned
    without re-extracting.
    """
    metadata = source_rules.validate_source_metadata(
        title=body.get("title"),
        source_type=body.get("type"),
        origin=body.get("origin"),
        custodian=body.get("custodian"),
        docket_ref=body.get("docketRef"),
        privilege_status=body.get("privilegeStatus"),
    )

    # Upload permission depends on the privilege being claimed. Without this,
    # a reviewer without clearance could create a withheld row that the
    # privilege-aware RLS policy then hides from its own author — or refuses
    # to insert at all. Whoever creates withheld material can always see it.
    if _is_withholding_privilege(metadata["privilegeStatus"]) and not _has_privilege_clearance(
        membership.role
    ):
        raise t.PermissionDenied(
            "Privilege clearance is required to upload attorney-client, "
            "work-product, or restricted material."
        )

    original_filename = source_rules.sanitize_filename(body.get("originalFilename") or "")
    raw_bytes = source_rules.decode_upload(
        body.get("contentBase64") or "", max_bytes=max_source_upload_bytes()
    )
    content_hash = source_rules.content_sha256(raw_bytes)

    idempotency_key = source_rules.validate_idempotency_key(body.get("idempotencyKey"))
    fingerprint = source_rules.idempotency_fingerprint(
        case_id=case_id,
        content_hash=content_hash,
        metadata=metadata,
        original_filename=original_filename,
    )

    prepared = {
        "metadata": metadata,
        "originalFilename": original_filename,
        "contentSha256": content_hash,
        "idempotencyKey": idempotency_key,
        "idempotencyFingerprint": fingerprint,
    }

    replay = None
    if idempotency_key is not None:
        replay = _idempotent_replay(
            session,
            reviewer,
            case_id=case_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            membership=membership,
        )
    return raw_bytes, prepared, replay


def _idempotent_replay(
    session: Session,
    reviewer: m.Reviewer,
    *,
    case_id: str,
    idempotency_key: str,
    fingerprint: str,
    membership: m.CaseMember,
) -> dict | None:
    existing = session.execute(
        select(m.SourceDocumentFile).where(
            m.SourceDocumentFile.case_id == case_id,
            m.SourceDocumentFile.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is None:
        return None
    if existing.idempotency_fingerprint != fingerprint:
        # The same key for a different document is client misuse. Answering
        # with the unrelated stored result would silently substitute one piece
        # of evidence for another.
        raise t.DuplicateConflict(
            "This idempotency key was already used for a different upload."
        )
    source = session.get(m.SourceDocument, existing.source_document_id)
    if source is None or not _source_visible_to(membership.role, source):
        raise t.NotFound("Source document not found.")
    return {
        "source": serialize_source(source, source_file_summary(session, source.id)),
        "possibleDuplicateSourceIds": [],
        "replayed": True,
    }


def create_source_op(
    session: Session,
    reviewer: m.Reviewer,
    *,
    case_id: str,
    prepared: dict,
    raw_bytes: bytes,
    extraction,
) -> dict:
    """Phase 3 of an upload: persist the document, its extraction, and audit.

    Runs in a fresh transaction after extraction, so the caller has already
    re-authenticated and re-checked membership.
    """
    membership = authorize_case_action(session, reviewer, case_id)
    case = session.get(m.CaseFile, case_id)
    if case is None:
        raise t.NotFound("Case not found.")

    metadata = prepared["metadata"]
    content_hash = prepared["contentSha256"]
    now = _now()

    duplicates = _visible_duplicate_source_ids(
        session, case_id=case_id, content_hash=content_hash, matter_role=membership.role
    )

    source = m.SourceDocument(
        id=_next_id(session, "source_id_seq", "src"),
        case_id=case_id,
        title=metadata["title"],
        type=metadata["type"],
        origin=metadata["origin"],
        custodian=metadata["custodian"],
        docket_ref=metadata["docketRef"],
        ingested_at=now,
        sha256=content_hash,
        privilege_status=metadata["privilegeStatus"],
        custody=source_rules.build_custody_record(
            at=now,
            reviewer_name=reviewer.name,
            matter_role=membership.role,
            original_filename=prepared["originalFilename"],
            content_hash=content_hash,
        ),
        active_extraction_run_id=None,
    )
    session.add(source)
    session.flush()

    try:
        session.add(
            m.SourceDocumentFile(
                source_document_id=source.id,
                case_id=case_id,
                content=raw_bytes,
                content_sha256=content_hash,
                mime_type=source_rules.PDF_MIME_TYPE,
                original_filename=prepared["originalFilename"],
                size_bytes=len(raw_bytes),
                page_count=extraction.page_count,
                uploaded_at=now,
                idempotency_key=prepared["idempotencyKey"],
                idempotency_fingerprint=prepared["idempotencyFingerprint"],
            )
        )
        session.flush()
    except IntegrityError as exc:
        # Two concurrent requests with the same key can both pass phase 1's
        # lookup. The loser lands here; it is a race, not an error.
        if "uq_source_file_idempotency" not in str(exc.orig):
            raise
        raise _ConcurrentIdempotentUpload() from exc

    # Both tables are append-only, so every column — including the manifest
    # hash that covers the pages — must be final before the first INSERT.
    # Identifiers are therefore generated up front and the manifest is computed
    # in memory, rather than inserting and patching the hash in afterwards.
    run = m.SourceExtractionRun(
        id=_next_id(session, "extraction_run_id_seq", "xrun"),
        source_document_id=source.id,
        case_id=case_id,
        started_at=now,
        completed_at=_now(),
        parser_version=extraction.parser_version,
        ocr_engine_version=extraction.ocr_engine_version,
        ocr_languages=extraction.ocr_languages,
        config_snapshot=extraction.config_snapshot,
        page_count=extraction.page_count,
        ocr_page_count=extraction.ocr_page_count,
        failed_page_count=extraction.failed_page_count,
        manifest_sha256="",
    )
    page_rows = [
        m.SourcePageExtraction(
            id=f"pg-{uuid.uuid4().hex[:12]}",
            extraction_run_id=run.id,
            source_document_id=source.id,
            case_id=case_id,
            page_number=page.page_number,
            extracted_text=page.text,
            extraction_method=page.method,
            ocr_confidence_bps=page.ocr_confidence_bps,
            text_truncated=page.text_truncated,
            failure_code=page.failure_code,
            failure_detail=page.failure_detail,
            text_sha256=page.text_sha256,
        )
        for page in extraction.pages
    ]
    run.manifest_sha256 = extraction_manifest_sha256(
        content_sha256=content_hash,
        source_document_id=source.id,
        run=run,
        pages=page_rows,
    )
    session.add(run)
    session.flush()
    for row in page_rows:
        session.add(row)
    session.flush()

    # Set-once activation, guarded in the database. Covered by the audit event
    # appended below; there is deliberately no way to re-point it here.
    session.execute(
        update(m.SourceDocument)
        .where(m.SourceDocument.id == source.id)
        .values(active_extraction_run_id=run.id)
    )
    session.flush()
    session.refresh(source)

    failed = extraction.failed_page_count
    event = _append_audit(
        session,
        case_id=case_id,
        at=now,
        actor=reviewer,
        action="source ingested",
        subject_type="source",
        subject_id=source.id,
        detail=(
            f"Uploaded {prepared['originalFilename']} ({len(raw_bytes)} bytes, "
            f"{extraction.page_count} pages; {extraction.ocr_page_count} OCR, "
            f"{failed} unreadable). Privilege: {metadata['privilegeStatus']}. "
            f"Content SHA-256 {content_hash}. Extraction manifest SHA-256 "
            f"{run.manifest_sha256}."
        ),
    )

    return {
        "source": serialize_source(source, source_file_summary(session, source.id)),
        "possibleDuplicateSourceIds": duplicates,
        "auditEvent": serialize_audit_event(event),
        "replayed": False,
    }


class _ConcurrentIdempotentUpload(Exception):
    """Internal signal: another request with the same key committed first."""


def resolve_idempotent_race(
    session: Session,
    reviewer: m.Reviewer,
    *,
    case_id: str,
    prepared: dict,
) -> dict:
    """After losing an idempotency race, return the winner's stored result."""
    membership = authorize_case_action(session, reviewer, case_id)
    replay = _idempotent_replay(
        session,
        reviewer,
        case_id=case_id,
        idempotency_key=prepared["idempotencyKey"],
        fingerprint=prepared["idempotencyFingerprint"],
        membership=membership,
    )
    if replay is None:
        raise t.DuplicateConflict("Concurrent upload could not be reconciled.")
    return replay


def list_source_pages_op(
    session: Session,
    reviewer: m.Reviewer,
    *,
    case_id: str,
    source_id: str,
    offset: int = 0,
    limit: int = 20,
) -> dict:
    membership = authorize_case_action(session, reviewer, case_id)
    source = session.get(m.SourceDocument, source_id)
    # Same anti-oracle rule as claim extraction: a source the reviewer may not
    # read is indistinguishable from one that does not exist.
    if (
        source is None
        or source.case_id != case_id
        or not _source_visible_to(membership.role, source)
    ):
        raise t.NotFound("Source document not found.")
    if source.active_extraction_run_id is None:
        raise t.NotFound("This source has no extracted pages.")

    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    total = session.execute(
        select(func.count())
        .select_from(m.SourcePageExtraction)
        .where(m.SourcePageExtraction.extraction_run_id == source.active_extraction_run_id)
    ).scalar_one()
    pages = list(
        session.execute(
            select(m.SourcePageExtraction)
            .where(m.SourcePageExtraction.extraction_run_id == source.active_extraction_run_id)
            .order_by(m.SourcePageExtraction.page_number)
            .offset(offset)
            .limit(limit)
        ).scalars()
    )
    return {
        "sourceId": source.id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "pages": [serialize_source_page(page) for page in pages],
    }


def _page_evidence(page: m.SourcePageExtraction | None) -> quote_rules.PageEvidence | None:
    if page is None:
        return None
    return quote_rules.PageEvidence(
        id=page.id,
        page_number=page.page_number,
        extracted_text=page.extracted_text,
        extraction_method=page.extraction_method,
        ocr_confidence_bps=page.ocr_confidence_bps,
        text_truncated=page.text_truncated,
    )


def _load_cited_page(
    session: Session, *, source: m.SourceDocument, page_id: str | None
) -> m.SourcePageExtraction | None:
    if page_id is None:
        return None
    page = session.get(m.SourcePageExtraction, page_id)
    if (
        page is None
        or page.source_document_id != source.id
        or page.case_id != source.case_id
        or page.extraction_run_id != source.active_extraction_run_id
    ):
        raise t.ValidationFailure("That page does not belong to the cited source document.")
    return page


def _enable_verification_write(session: Session) -> None:
    """Announce a managed verification write for this transaction.

    The database rejects changes to a claim's verification columns unless this
    is set, so no ordinary claim update can relabel a quote as verified.
    """
    session.execute(text("SELECT set_config('atlas_argus.verification_write','1',true)"))


def _apply_quote_verification(
    session: Session,
    *,
    claim: m.Claim,
    page: m.SourcePageExtraction | None,
) -> quote_rules.VerificationOutcome:
    """Evaluate and persist verification for a claim's current quote/page.

    The single path through which ``quote_verification`` may change.
    """
    outcome = quote_rules.evaluate_quote(
        quote=claim.quote,
        page=_page_evidence(page),
        min_ocr_confidence_percent=min_ocr_confidence_for_auto_verify(),
    )
    if outcome.is_rejection:
        raise t.ValidationFailure(outcome.rejection)

    from .ingestion import normalize_quote_text

    basis = quote_rules.verification_basis_sha256(
        claim_id=claim.id,
        source_document_id=claim.source_document_id,
        source_page_extraction_id=page.id if page is not None else None,
        normalized_quote=normalize_quote_text(claim.quote),
    )
    _enable_verification_write(session)
    claim.source_page_extraction_id = page.id if page is not None else None
    claim.quote_verification = outcome.state
    claim.quote_verification_basis_sha256 = (
        basis if outcome.state != "legacy_unverified" else None
    )
    return outcome


def create_claim_op(
    session: Session,
    reviewer: m.Reviewer,
    *,
    source_document_id: str,
    text_: str,
    page_ref: str,
    quote: str,
    confidence: float,
    evidence_quality: str,
    case_id: str | None = None,
    source_page_extraction_id: str | None = None,
) -> dict:
    error = claims_rules.validate_new_claim(text_, page_ref, quote, confidence)
    if error:
        raise t.ValidationFailure(error)
    if evidence_quality not in t.EVIDENCE_QUALITIES:
        raise t.ValidationFailure(f"Unknown evidence quality: {evidence_quality}.")
    if case_id is not None:
        authorize_case_action(session, reviewer, case_id)
    source = session.get(m.SourceDocument, source_document_id)
    if source is None:
        raise t.NotFound("Source document not found.")
    if case_id is not None and source.case_id != case_id:
        raise t.NotFound("Source document not found in this case.")
    # A source the reviewer may not read must be indistinguishable from one
    # that was never ingested. Distinct answers here would otherwise let any
    # reviewer walk the source-ID space and learn which documents in their own
    # matter are privilege-withheld, and which IDs belong to matters they are
    # walled off from. The caller's own membership in the route's case is
    # checked above, so a genuine access error still surfaces as 403.
    membership = _case_membership(session, reviewer, source.case_id)
    if (
        membership is None
        or not membership.is_active
        or not _source_visible_to(membership.role, source)
    ):
        raise t.NotFound("Source document not found.")

    page = _load_cited_page(session, source=source, page_id=source_page_extraction_id)

    now = _now()
    claim = m.Claim(
        id=_next_id(session, "claim_id_seq", "clm"),
        case_id=source.case_id,
        text=text_.strip(),
        source_document_id=source.id,
        page_ref=page_ref.strip(),
        quote=quote.strip(),
        confidence=confidence,
        evidence_quality=evidence_quality,
        status="unreviewed",
        # Privilege is inherited from the source, never chosen at extraction.
        privilege_status=source.privilege_status,
        report_eligibility=compute_claim_eligibility("unreviewed", source.privilege_status, []),
        reviewer=None,
        updated_at=now,
        quote_verification="legacy_unverified",
    )
    session.add(claim)
    session.flush()

    # Raises 422 if the quote is confidently absent from complete page text.
    _apply_quote_verification(session, claim=claim, page=page)
    claim.report_eligibility = compute_claim_eligibility(
        claim.status, claim.privilege_status, [], claim.quote_verification
    )
    session.flush()

    event = _append_audit(
        session,
        case_id=claim.case_id,
        at=now,
        actor=reviewer,
        action="claim extracted",
        subject_type="claim",
        subject_id=claim.id,
        new_status=claim.status,
        detail=(
            f"Extracted from {source.title}, {claim.page_ref}. "
            f"Privilege inherited from source: {source.privilege_status}. "
            f"Quote verification: {_verification_detail(claim, page)}."
        ),
    )
    return {
        "claim": serialize_claim(session, claim),
        "auditEvent": serialize_audit_event(event),
    }


def _verification_detail(claim: m.Claim, page: m.SourcePageExtraction | None) -> str:
    """Human-readable verification summary for the audit trail. Names the
    extraction method so 'verified' never reads as equally strong regardless of
    whether a human-readable text layer or an OCR guess produced it."""
    state = claim.quote_verification
    if state == "legacy_unverified":
        return "not checked (source predates document ingestion)"
    if page is None:
        return state
    if state == "verified_exact":
        basis = "native PDF text" if page.extraction_method == "native" else "OCR-extracted text"
        return f"exact match against {basis} on page {page.page_number}"
    if state == "manual_verification_required":
        if page.extraction_method == "failed":
            return f"page {page.page_number} could not be extracted; needs human attestation"
        if page.text_truncated:
            return f"page {page.page_number} text was truncated; needs human attestation"
        return f"low-confidence OCR on page {page.page_number}; needs human attestation"
    return state


def _record_quote_verification_event(
    session: Session,
    *,
    claim: m.Claim,
    reviewer: m.Reviewer,
    matter_role: str,
    event_type: str,
    reasoning: str,
    previous_verification: str,
    at: datetime,
) -> m.ClaimQuoteVerification:
    row = m.ClaimQuoteVerification(
        id=f"cqv-{uuid.uuid4().hex[:12]}",
        case_id=claim.case_id,
        claim_id=claim.id,
        reviewer_id=reviewer.id,
        matter_role=matter_role,
        at=at,
        event_type=event_type,
        reasoning=reasoning,
        previous_verification=previous_verification,
        new_verification=claim.quote_verification,
        verification_basis_sha256=claim.quote_verification_basis_sha256,
        source_page_extraction_id=claim.source_page_extraction_id,
    )
    session.add(row)
    session.flush()
    return row


def manually_verify_claim_quote_op(
    session: Session,
    reviewer: m.Reviewer,
    *,
    case_id: str,
    claim_id: str,
    reasoning: str,
) -> dict:
    """Record that a qualified reviewer confirmed this quote against the
    original document.

    Two constraints make this an attestation rather than a formality. It
    requires privilege clearance in this matter, and the verifier must not be
    the person who created the claim — a single reviewer must not be able to
    both assert a quote and clear it. This build has no in-app page-image
    viewer, so the reviewer is attesting they consulted the original evidence
    outside Atlas Argus; the UI says so explicitly.
    """
    membership = authorize_case_action(session, reviewer, case_id)
    if not _has_privilege_clearance(membership.role):
        raise t.PermissionDenied(
            "A Senior Aviation Counsel matter role is required to attest to a quote."
        )

    cleaned = (reasoning or "").strip()
    if len(cleaned) < 20:
        raise t.ValidationFailure(
            "Verification reasoning must be at least 20 characters."
        )

    claim = session.get(m.Claim, claim_id)
    if claim is None or claim.case_id != case_id:
        raise t.NotFound("Claim not found.")
    if not _claim_visible_to(membership.role, claim):
        raise t.NotFound("Claim not found.")
    if claim.quote_verification != "manual_verification_required":
        raise t.ValidationFailure(
            "This claim's quote is not awaiting manual verification."
        )

    author_id = _claim_author_reviewer_id(session, claim)
    if author_id is not None and author_id == reviewer.id:
        raise t.PermissionDenied(
            "A claim's quote must be verified by someone other than the reviewer "
            "who created it."
        )

    now = _now()
    previous = claim.quote_verification
    _enable_verification_write(session)
    claim.quote_verification = "manually_verified"
    claim.updated_at = now
    claim.report_eligibility = compute_claim_eligibility(
        claim.status,
        claim.privilege_status,
        [conflict.status for conflict in _related_conflicts(session, claim.id)],
        claim.quote_verification,
    )
    session.flush()

    _record_quote_verification_event(
        session,
        claim=claim,
        reviewer=reviewer,
        matter_role=membership.role,
        event_type="manual_verified",
        reasoning=cleaned,
        previous_verification=previous,
        at=now,
    )
    event = _append_audit(
        session,
        case_id=case_id,
        at=now,
        actor=reviewer,
        action="claim quote manually verified",
        subject_type="claim",
        subject_id=claim.id,
        previous_status=previous,
        new_status=claim.quote_verification,
        detail=(
            f"Attested against the original document outside Atlas Argus. {cleaned}"
        ),
        report_eligibility_changed=True,
    )
    return {
        "claim": serialize_claim(session, claim),
        "auditEvent": serialize_audit_event(event),
    }


def _claim_author_reviewer_id(session: Session, claim: m.Claim) -> str | None:
    """Who created this claim, from the audit trail — the authoritative record
    of authorship, since Claim has no author column."""
    return session.execute(
        select(m.AuditEvent.actor_reviewer_id)
        .where(
            m.AuditEvent.case_id == claim.case_id,
            m.AuditEvent.subject_type == "claim",
            m.AuditEvent.subject_id == claim.id,
            m.AuditEvent.action == "claim extracted",
        )
        .order_by(m.AuditEvent.seq)
        .limit(1)
    ).scalar_one_or_none()


def flag_conflict_op(
    session: Session,
    reviewer: m.Reviewer,
    *,
    claim_a_id: str,
    claim_b_id: str,
    conflict_type: str,
    severity: str,
    summary: str,
    case_id: str | None = None,
) -> dict:
    error = claims_rules.validate_flag_conflict(claim_a_id, claim_b_id, summary)
    if error:
        raise t.ValidationFailure(error)
    if conflict_type not in t.CONFLICT_TYPES:
        raise t.ValidationFailure(f"Unknown conflict type: {conflict_type}.")
    if severity not in t.CONFLICT_SEVERITIES:
        raise t.ValidationFailure(f"Unknown severity: {severity}.")
    if case_id is not None:
        authorize_case_action(session, reviewer, case_id)
    try:
        claim_a = _require_claim_visible(session, reviewer, claim_a_id)
        claim_b = _require_claim_visible(session, reviewer, claim_b_id)
    except (t.NotFound, t.PermissionDenied):
        # A claim the reviewer may not see must be indistinguishable from one
        # that does not exist. Claim identifiers come from a single global
        # sequence, so distinct answers here would let any reviewer walk the
        # ID space and learn which claims in their own matter are
        # privilege-withheld, and which IDs belong to matters they are walled
        # off from. The caller's own membership in the route's case is checked
        # separately above, so a genuine access error still surfaces as 403.
        raise t.ValidationFailure("Both claims must exist.") from None
    if case_id is not None and (claim_a.case_id != case_id or claim_b.case_id != case_id):
        raise t.ValidationFailure("Both claims must belong to this case.")
    if claim_a.case_id != claim_b.case_id:
        raise t.ValidationFailure("Both claims must belong to the same case.")

    locked_claims = _lock_claim_rows(session, [claim_a.id, claim_b.id])
    claim_a = locked_claims[claim_a.id]
    claim_b = locked_claims[claim_b.id]

    duplicate = session.execute(
        select(m.Conflict).where(
            m.Conflict.case_id == claim_a.case_id,
            m.Conflict.status.in_(claims_rules.OPEN_CONFLICT_STATUSES),
            or_(
                (m.Conflict.claim_a_id == claim_a_id) & (m.Conflict.claim_b_id == claim_b_id),
                (m.Conflict.claim_a_id == claim_b_id) & (m.Conflict.claim_b_id == claim_a_id),
            ),
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise t.DuplicateConflict(
            f"These claims already have an open conflict ({duplicate.id.upper()})."
        )

    now = _now()
    conflict = m.Conflict(
        id=_next_id(session, "conflict_id_seq", "cf"),
        case_id=claim_a.case_id,
        claim_a_id=claim_a.id,
        claim_b_id=claim_b.id,
        conflict_type=conflict_type,
        severity=severity,
        status="unresolved",
        summary=summary.strip(),
        created_at=now,
        # A freshly flagged conflict disputes both its claims.
        claim_a_disposition="disputed",
        claim_b_disposition="disputed",
    )
    session.add(conflict)
    try:
        session.flush()
    except IntegrityError as exc:
        if "uq_open_conflict_claim_pair" in str(exc):
            raise t.DuplicateConflict("These claims already have an open conflict.") from exc
        raise

    claim_events: list[m.AuditEvent] = []
    for claim in (claim_a, claim_b):
        previous_status = claim.status
        previous_eligibility = claim.report_eligibility
        claim.reviewer = reviewer.name
        claim.updated_at = now
        _recompute_claim_status(session, claim)
        claim_events.append(
            _append_audit(
                session,
                case_id=conflict.case_id,
                at=now,
                actor=reviewer,
                action="claim status updated",
                subject_type="claim",
                subject_id=claim.id,
                previous_status=previous_status,
                new_status=claim.status,
                detail=(
                    f"Flagged in {conflict.id.upper()}. Report eligibility: "
                    f"{previous_eligibility} → {claim.report_eligibility}"
                ),
                report_eligibility_changed=previous_eligibility != claim.report_eligibility,
            )
        )

    conflict_event = _append_audit(
        session,
        case_id=conflict.case_id,
        at=now,
        actor=reviewer,
        action="conflict flagged",
        subject_type="conflict",
        subject_id=conflict.id,
        new_status="unresolved",
        detail=f"{conflict.summary} (severity: {severity}, type: {conflict_type}).",
        report_eligibility_changed=True,
    )

    return {
        "conflict": serialize_conflict(session, conflict),
        "claims": [serialize_claim(session, claim_a), serialize_claim(session, claim_b)],
        "auditEvents": [serialize_audit_event(e) for e in (conflict_event, *claim_events)],
    }


def apply_decision_op(
    session: Session,
    reviewer: m.Reviewer,
    conflict_id: str,
    *,
    decision_type: str,
    selected_claim_id: str | None,
    reasoning: str,
    expected_version: int,
    case_id: str | None = None,
) -> dict:
    if case_id is not None:
        authorize_case_action(session, reviewer, case_id)
    conflict = session.execute(
        select(m.Conflict).where(m.Conflict.id == conflict_id).with_for_update()
    ).scalar_one_or_none()
    if conflict is None:
        raise t.NotFound("Conflict not found.")
    if case_id is not None and conflict.case_id != case_id:
        raise t.NotFound("Conflict not found in this case.")
    membership = authorize_case_action(session, reviewer, conflict.case_id)
    _check_expected_version(conflict.version, expected_version, "Conflict")
    if decision_type not in t.DECISION_TYPES:
        raise t.ValidationFailure(f"Unknown decision type: {decision_type}.")

    allowed, reason = decision_permission(decision_type, membership.role, conflict.severity)
    if not allowed:
        raise t.PermissionDenied(reason or "Not permitted.")
    error = decision_rules.validate_decision(decision_type, selected_claim_id, reasoning)
    if error:
        raise t.ValidationFailure(error)
    if decision_type == "mark_source_unreliable" and selected_claim_id not in (
        conflict.claim_a_id,
        conflict.claim_b_id,
    ):
        raise t.ValidationFailure("The unreliable-source claim must belong to this conflict.")

    claim_a = session.get(m.Claim, conflict.claim_a_id)
    claim_b = session.get(m.Claim, conflict.claim_b_id)
    if claim_a is None or claim_b is None:
        raise RuntimeError("Conflict references missing claim evidence.")
    if not _claim_visible_to(membership.role, claim_a) or not _claim_visible_to(
        membership.role, claim_b
    ):
        raise t.PermissionDenied("Privilege clearance is required for this evidence.")

    locked_claims = _lock_claim_rows(session, [claim_a.id, claim_b.id])
    claim_a = locked_claims[claim_a.id]
    claim_b = locked_claims[claim_b.id]

    # Decisions are immutable evidence chained per case. Reuse the case audit
    # lock so the decision and all audit effects commit in one deterministic
    # order relative to packet snapshots and other case mutations.
    _lock_audit_chain(session, conflict.case_id)
    now = _now()
    previous_conflict_status = conflict.status
    transition = decision_rules.transition_for(
        decision_type, selected_claim_id, conflict.claim_a_id
    )
    conflict.status = transition.conflict_status
    # This conflict's verdict on each of its claims. The claim's global status
    # is re-derived from this plus every other conflict it is in — deciding one
    # conflict no longer overwrites a disposition reached in another.
    conflict.claim_a_disposition = transition.claim_a_status
    conflict.claim_b_disposition = transition.claim_b_status
    conflict.version += 1
    session.flush()

    claim_events: list[m.AuditEvent] = []
    for claim in (claim_a, claim_b):
        previous_status = claim.status
        previous_eligibility = claim.report_eligibility
        claim.reviewer = reviewer.name
        claim.updated_at = now
        _recompute_claim_status(session, claim)
        if previous_status != claim.status or previous_eligibility != claim.report_eligibility:
            claim_events.append(
                _append_audit(
                    session,
                    case_id=conflict.case_id,
                    at=now,
                    actor=reviewer,
                    action="claim status updated",
                    subject_type="claim",
                    subject_id=claim.id,
                    previous_status=previous_status,
                    new_status=claim.status,
                    detail=(
                        f"Report eligibility: {previous_eligibility} → "
                        f"{claim.report_eligibility}"
                    ),
                    report_eligibility_changed=previous_eligibility != claim.report_eligibility,
                )
            )

    resolved_selected = (
        conflict.claim_a_id
        if decision_type == "accept_claim_a"
        else conflict.claim_b_id
        if decision_type == "accept_claim_b"
        else selected_claim_id
    )
    previous_decision_hash = _latest_review_decision_hash(session, conflict.case_id)
    decision = m.ReviewDecision(
        id=f"dec-{uuid.uuid4().hex[:12]}",
        conflict_id=conflict.id,
        decision_type=decision_type,
        selected_claim_id=resolved_selected,
        reasoning=reasoning.strip(),
        reviewer_name=reviewer.name,
        reviewer_role=membership.role,
        created_at=now,
        previous_status=previous_conflict_status,
        new_status=conflict.status,
        report_impact=decision_rules.report_impact_summary(
            claim_a.report_eligibility, claim_b.report_eligibility, decision_type
        ),
        previous_integrity_hash=previous_decision_hash,
        integrity_hash="",
    )
    decision.integrity_hash = chain_sha256(
        previous_hash=previous_decision_hash,
        content=review_decision_content(decision),
    )
    session.add(decision)
    session.flush()

    conflict_event = _append_audit(
        session,
        case_id=conflict.case_id,
        at=now,
        actor=reviewer,
        action=f"decision: {t.DECISION_LABELS[decision_type]}",
        subject_type="conflict",
        subject_id=conflict.id,
        previous_status=previous_conflict_status,
        new_status=conflict.status,
        detail=decision.reasoning,
    )

    return {
        "conflict": serialize_conflict(session, conflict),
        "claims": [serialize_claim(session, claim_a), serialize_claim(session, claim_b)],
        "decision": serialize_decision(decision),
        "auditEvents": [serialize_audit_event(e) for e in (conflict_event, *claim_events)],
    }


def save_section_op(
    session: Session,
    reviewer: m.Reviewer,
    *,
    title: str,
    paragraph_ref: str,
    text_: str,
    claim_ids: list[str],
    section_id: str | None = None,
    expected_version: int | None = None,
    case_id: str | None = None,
) -> dict:
    section: m.ReportSection | None = None
    if section_id is not None:
        if case_id is not None:
            authorize_case_action(session, reviewer, case_id)
        section = session.execute(
            select(m.ReportSection).where(m.ReportSection.id == section_id).with_for_update()
        ).scalar_one_or_none()
        if section is None:
            raise t.NotFound("Report section not found.")
        if case_id is not None and section.case_id != case_id:
            raise t.NotFound("Report section not found in this case.")
        membership = authorize_case_action(session, reviewer, section.case_id)
        target_case_id = section.case_id
        _check_expected_version(section.version, expected_version, "Report section")
    else:
        case, membership = _selected_case_for_reviewer(session, reviewer, case_id)
        target_case_id = case.id

    # Paragraph references are case-unique and positions are allocated from
    # the current maximum. Serialize the validation/read/write sequence so two
    # concurrent drafts (or revisions of different sections) cannot both pass
    # validation and leave one request as an unhandled unique-index failure or
    # duplicate display position.
    _lock_report_section_catalog(session, target_case_id)
    other_sections = [
        (s.id, s.paragraph_ref, s.title)
        for s in session.execute(
            select(m.ReportSection).where(m.ReportSection.case_id == target_case_id)
        ).scalars()
    ]
    # Only claims this reviewer is cleared to see are "known" to validation: a
    # citation they may not read must fail with the same "Cited claim not
    # found" message as one that does not exist, or the validator becomes a
    # privilege oracle over a dense, enumerable claim-ID space. The
    # _require_claim_visible pass below stays as a backstop.
    cleared = _has_privilege_clearance(membership.role)
    known_claim_ids = {
        claim_id
        for claim_id, privilege_status in session.execute(
            select(m.Claim.id, m.Claim.privilege_status).where(
                m.Claim.case_id == target_case_id
            )
        )
        if cleared or not _is_withholding_privilege(privilege_status)
    }
    error = section_rules.validate_section(
        title, paragraph_ref, text_, claim_ids, other_sections, known_claim_ids, section_id
    )
    if error:
        raise t.ValidationFailure(error)
    for claim_id in claim_ids:
        claim = _require_claim_visible(session, reviewer, claim_id)
        if claim.case_id != target_case_id:
            raise t.ValidationFailure("Report citations must belong to the section case.")

    now = _now()
    new_impact = _section_impact(session, paragraph_ref.strip(), claim_ids)

    if section is not None:
        previous_impact = _section_impact(
            session, section.paragraph_ref, list(section.claim_ids)
        )
        changes = section_rules.revision_changes(
            section.title,
            section.paragraph_ref,
            section.text,
            list(section.claim_ids),
            title.strip(),
            paragraph_ref.strip(),
            text_.strip(),
            claim_ids,
        )
        section.title = title.strip()
        section.paragraph_ref = paragraph_ref.strip()
        section.text = text_.strip()
        section.claim_ids = claim_ids
        section.version += 1
        session.flush()
        _append_section_revision(
            session,
            section=section,
            reviewer=reviewer,
            at=now,
            reason=changes,
        )
        event = _append_audit(
            session,
            case_id=section.case_id,
            at=now,
            actor=reviewer,
            action="report section revised",
            subject_type="report_section",
            subject_id=section.id,
            previous_status=previous_impact.status,
            new_status=new_impact.status,
            detail=f"¶ {section.paragraph_ref} “{section.title}”: {changes}.",
            report_eligibility_changed=previous_impact.status != new_impact.status,
        )
    else:
        max_position = session.execute(
            select(func.coalesce(func.max(m.ReportSection.position), -1)).where(
                m.ReportSection.case_id == target_case_id
            )
        ).scalar_one()
        section = m.ReportSection(
            id=_next_id(session, "section_id_seq", "rpt"),
            case_id=target_case_id,
            title=title.strip(),
            paragraph_ref=paragraph_ref.strip(),
            text=text_.strip(),
            claim_ids=claim_ids,
            position=max_position + 1,
            active_revision_id=None,
        )
        session.add(section)
        session.flush()
        _append_section_revision(
            session,
            section=section,
            reviewer=reviewer,
            at=now,
            reason="Initial draft.",
        )
        event = _append_audit(
            session,
            case_id=section.case_id,
            at=now,
            actor=reviewer,
            action="report section drafted",
            subject_type="report_section",
            subject_id=section.id,
            new_status=new_impact.status,
            detail=(
                f"¶ {section.paragraph_ref} “{section.title}” drafted, citing "
                f"{len(claim_ids)} claim(s): {', '.join(claim_ids)}."
            ),
        )

    return {
        "section": serialize_section(session, section),
        "auditEvent": serialize_audit_event(event),
    }


def approve_section_op(
    session: Session,
    reviewer: m.Reviewer,
    section_id: str,
    *,
    expected_version: int,
    case_id: str | None = None,
) -> dict:
    if case_id is not None:
        authorize_case_action(session, reviewer, case_id)
    section = session.execute(
        select(m.ReportSection).where(m.ReportSection.id == section_id).with_for_update()
    ).scalar_one_or_none()
    if section is None:
        raise t.NotFound("Report section not found.")
    if case_id is not None and section.case_id != case_id:
        raise t.NotFound("Report section not found in this case.")
    membership = authorize_case_action(session, reviewer, section.case_id)
    allowed, reason = packet_permission("production", membership.role)
    if not allowed:
        raise t.PermissionDenied(reason or "Not permitted.")
    _check_expected_version(section.version, expected_version, "Report section")
    now = _now()
    section.version += 1
    revision = _append_section_revision(
        session,
        section=section,
        reviewer=reviewer,
        at=now,
        reason="Approved complete rendered section for production.",
        approval_state="approved",
    )
    impact = _section_impact(session, section.paragraph_ref, list(section.claim_ids))
    event = _append_audit(
        session,
        case_id=section.case_id,
        at=now,
        actor=reviewer,
        action="report section approved",
        subject_type="report_section",
        subject_id=section.id,
        new_status=impact.status,
        detail=f"¶ {section.paragraph_ref} “{section.title}” approved for production as revision {revision.id}.",
    )
    return {
        "section": serialize_section(session, section),
        "auditEvent": serialize_audit_event(event),
    }


def generate_packet_op(
    session: Session,
    reviewer: m.Reviewer,
    *,
    packet_type: str,
    case_id: str | None = None,
) -> dict:
    """Build, redact, render, and hash-stamp an evidence packet server-side —
    the disclosure doctrine is enforced where a tampered client cannot reach
    it — then record the export on the audit trail."""
    from .packet import (
        build_packet,
        new_packet_id,
        packet_filename,
        render_packet_body,
        render_packet_document,
        sha256_hex,
    )

    if packet_type not in ("internal", "production"):
        raise t.ValidationFailure(f"Unknown packet type: {packet_type}.")
    case, membership = _selected_case_for_reviewer(session, reviewer, case_id)
    allowed, reason = packet_permission(packet_type, membership.role)
    if not allowed:
        raise t.PermissionDenied(reason or "Not permitted.")

    # Every in-scope case mutation must append to this audit chain before it
    # can commit. Holding its transaction lock across all packet reads gives
    # build_packet one coherent committed case snapshot at READ COMMITTED;
    # concurrent mutations either committed before this point or wait until
    # the artifact and its generation audit event commit.
    _lock_audit_chain(session, case.id)
    now = _now()
    packet = build_packet(
        session,
        packet_type,
        case_id=case.id,
        packet_id=new_packet_id(),
        now=iso_z(now),
        generated_by_name=reviewer.name,
        generated_by_role=membership.role,
    )
    entry_limit = max_packet_entries()
    if len(packet.entries) > entry_limit:
        raise t.ValidationFailure(
            f"Packet has {len(packet.entries)} section entries; limit is {entry_limit}."
        )
    body = render_packet_body(packet)
    sha256 = sha256_hex(body)
    document = render_packet_document(packet, body, sha256)
    document_bytes = _utf8_bytes(document)
    document_limit = max_packet_document_bytes()
    if document_bytes > document_limit:
        raise t.ValidationFailure(
            f"Packet document is {document_bytes} bytes; limit is {document_limit}."
        )
    document_sha256 = sha256_hex(document)
    filename = packet_filename(packet)
    _lock_packet_artifact_chain(session, packet.case.id)
    previous_packet_hash = _latest_packet_artifact_hash(session, packet.case.id)
    audit_root_before_generation = _latest_audit_hash(session, packet.case.id)
    manifest = {
        "packetId": packet.packet_id,
        "packetType": packet.type,
        "generatedAt": packet.generated_at,
        "generator": "atlas_argus.server.packet.v1",
        "auditRootBeforeGenerationSha256": audit_root_before_generation,
        "bodySha256": sha256,
        "documentSha256": document_sha256,
        "previousPacketIntegrityHash": previous_packet_hash,
        "entries": [
            {
                "sectionId": entry.artifact_section_id,
                "title": entry.title,
                "paragraphRef": entry.paragraph_ref,
                "impactStatus": entry.impact_status,
                "disposition": entry.disposition,
                "reason": entry.reason,
                **(
                    {
                        "revisionId": entry.revision_id,
                        "revisionContentSha256": entry.revision_content_sha256,
                        "claimIds": list(entry.claim_ids),
                    }
                    if packet.type != "production" or entry.disposition == "included"
                    else {}
                ),
            }
            for entry in packet.entries
        ],
        "citedClaimIds": [pc.claim.id for pc in packet.cited_claims],
        "citedSourceIds": [source.id for source in packet.cited_sources],
        "decisionIds": [decision.id for decision, _summary in (packet.decision_log or [])],
    }
    if packet.type != "production":
        manifest["generatedByReviewerId"] = reviewer.id
    manifest_sha256 = canonical_sha256(manifest)
    artifact_sha256 = canonical_sha256(
        {
            "schema": "atlas_argus.packet_artifact.v1",
            "documentSha256": document_sha256,
            "manifestSha256": manifest_sha256,
        }
    )
    manifest["manifestSha256"] = manifest_sha256
    manifest["artifactSha256"] = artifact_sha256
    manifest_bytes = _utf8_bytes(canonical_json(manifest))
    manifest_limit = max_packet_manifest_bytes()
    if manifest_bytes > manifest_limit:
        raise t.ValidationFailure(
            f"Packet manifest is {manifest_bytes} bytes; limit is {manifest_limit}."
        )
    artifact_bytes = document_bytes + manifest_bytes
    artifact_limit = max_packet_artifact_bytes()
    if artifact_bytes > artifact_limit:
        raise t.ValidationFailure(
            f"Packet artifact is {artifact_bytes} bytes; limit is {artifact_limit}."
        )

    # Rendered from the document that was just hashed, so the PDF and the HTML
    # cannot describe different packets. Both are stored: the PDF is what gets
    # produced to opposing counsel, and an artifact record that could not
    # reproduce it byte-for-byte would not be much of a record.
    pdf_bytes, pdf_hash = _render_packet_pdf(
        packet, document, sha256, generated_at=now
    )

    artifact = m.PacketArtifact(
        id=packet.packet_id,
        case_id=packet.case.id,
        packet_type=packet.type,
        generated_at=now,
        generated_by_reviewer_id=reviewer.id,
        generated_by_name=reviewer.name,
        generated_by_role=membership.role,
        filename=filename,
        body_sha256=sha256,
        document=document,
        manifest=manifest,
        pdf=pdf_bytes,
        pdf_sha256=pdf_hash,
        previous_integrity_hash=previous_packet_hash,
        integrity_hash="",
    )
    artifact.integrity_hash = chain_sha256(
        previous_hash=previous_packet_hash,
        content=packet_artifact_content(artifact),
    )
    session.add(artifact)

    event = _append_audit(
        session,
        case_id=packet.case.id,
        at=now,
        actor=reviewer,
        action="evidence packet generated",
        subject_type="export",
        subject_id=packet.packet_id,
        detail=(
            f"{packet_type} packet: {packet.stats['included']} section(s) included, "
            f"{packet.stats['excluded']} excluded, {packet.stats['withheld']} withheld "
            f"under privilege. Body SHA-256 {sha256}. Artifact SHA-256 {artifact_sha256}."
            + (f" PDF SHA-256 {pdf_hash}." if pdf_hash else " PDF not rendered.")
        ),
    )
    return {
        "packetId": packet.packet_id,
        "packetType": packet.type,
        "generatedAt": packet.generated_at,
        "generatedByName": packet.generated_by_name,
        "generatedByRole": packet.generated_by_role,
        "stats": packet.stats,
        "entries": [
            {
                "sectionId": entry.artifact_section_id,
                "title": entry.title,
                "paragraphRef": entry.paragraph_ref,
                "impactStatus": entry.impact_status,
                "disposition": entry.disposition,
                "reason": entry.reason,
                **(
                    {"revisionId": entry.revision_id}
                    if packet.type != "production" or entry.disposition == "included"
                    else {}
                ),
            }
            for entry in packet.entries
        ],
        "sha256": sha256,
        "documentSha256": document_sha256,
        "manifestSha256": manifest_sha256,
        "artifactSha256": artifact_sha256,
        "packetIntegrityHash": artifact.integrity_hash,
        "filename": filename,
        "document": document,
        "auditEvent": serialize_audit_event(event),
    }
