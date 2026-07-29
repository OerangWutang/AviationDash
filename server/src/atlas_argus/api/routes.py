from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import auth, ingestion, services
from ..config import cookie_secure
from ..db import models as m
from ..db.session import (
    get_session,
    get_unmanaged_session,
    set_rls_reviewer_context,
    short_request_session,
)
from ..domain.permissions import admin_permission
from ..domain.types import DomainError, RateLimited, Unauthorized, ValidationFailure
from .schemas import (
    CaseMemberRequest,
    ChangePasswordRequest,
    CreateReviewerRequest,
    DecisionRequest,
    FlagConflictRequest,
    GeneratePacketRequest,
    LoginRequest,
    MfaCodeRequest,
    NewClaimRequest,
    NewMatterRequest,
    NewSourceRequest,
    ResetReviewerPasswordRequest,
    SectionRequest,
    SectionUpdateRequest,
    VerifyClaimQuoteRequest,
)

router = APIRouter(prefix="/api")


def require_reviewer(request: Request, session: Session = Depends(get_session)) -> m.Reviewer:
    reviewer = auth.resolve_session(session, request.cookies.get(auth.SESSION_COOKIE))
    if reviewer is None:
        raise Unauthorized("Not signed in.")
    set_rls_reviewer_context(session, reviewer.id)
    if reviewer.must_change_password:
        from ..domain.types import PermissionDenied

        raise PermissionDenied("Password change required before using the case API.")
    return reviewer


def require_unmanaged_reviewer(
    request: Request, session: Session = Depends(get_unmanaged_session)
) -> m.Reviewer:
    """Resolve a reviewer in the same manually committed session as MFA routes.

    Using ``require_reviewer`` here would resolve the ORM object through a
    separate managed transaction. Sharing this dependency keeps reviewer
    updates and throttle side effects attached to one session.
    """
    reviewer = auth.resolve_session(session, request.cookies.get(auth.SESSION_COOKIE))
    if reviewer is None:
        raise Unauthorized("Not signed in.")
    set_rls_reviewer_context(session, reviewer.id)
    if reviewer.must_change_password:
        from ..domain.types import PermissionDenied

        raise PermissionDenied("Password change required before using the case API.")
    return reviewer


def require_session_reviewer(
    request: Request, session: Session = Depends(get_session)
) -> m.Reviewer:
    reviewer = auth.resolve_session(session, request.cookies.get(auth.SESSION_COOKIE))
    if reviewer is None:
        raise Unauthorized("Not signed in.")
    set_rls_reviewer_context(session, reviewer.id)
    return reviewer


def require_admin_reviewer(
    reviewer: m.Reviewer = Depends(require_reviewer),
) -> m.Reviewer:
    allowed, reason = admin_permission(reviewer.role)
    if not allowed:
        from ..domain.types import PermissionDenied

        raise PermissionDenied(reason or "Not permitted.")
    return reviewer


def require_mfa_reviewer(
    request: Request,
    session: Session = Depends(get_session),
) -> m.Reviewer:
    reviewer = require_reviewer(request, session)
    auth.require_mfa(session, reviewer, request.cookies.get(auth.SESSION_COOKIE))
    return reviewer


def require_mfa_admin_reviewer(
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> m.Reviewer:
    allowed, reason = admin_permission(reviewer.role)
    if not allowed:
        from ..domain.types import PermissionDenied

        raise PermissionDenied(reason or "Not permitted.")
    return reviewer


def _serialize_reviewer(reviewer: m.Reviewer) -> dict:
    return {"id": reviewer.id, "name": reviewer.name, "role": reviewer.role}


def _session_expiry(session: Session, token: str | None) -> str:
    auth_session = auth.resolve_auth_session(session, token)
    if auth_session is None:
        raise Unauthorized("Not signed in.")
    return auth_session.expires_at.isoformat().replace("+00:00", "Z")


def _commit_mfa_attempt(session: Session, operation: Callable[[], None]) -> None:
    """Persist invalid-code counters while keeping other failures atomic."""
    try:
        operation()
        session.commit()
    except (ValidationFailure, RateLimited):
        session.commit()
        raise
    except Exception:
        session.rollback()
        raise


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


@router.post("/auth/login")
def login(
    body: LoginRequest,
    response: Response,
    session: Session = Depends(get_unmanaged_session),
) -> dict:
    try:
        reviewer, token = auth.login(session, body.username, body.password)
        session.commit()
    except DomainError:
        session.commit()
        raise
    except Exception:
        session.rollback()
        raise
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=int(auth.SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
        path="/",
    )
    return {
        "reviewer": _serialize_reviewer(reviewer),
        "mustChangePassword": reviewer.must_change_password,
        "mfa": auth.mfa_status(session, reviewer, token),
        "expiresAt": _session_expiry(session, token),
    }


@router.post("/auth/logout")
def logout(
    request: Request, response: Response, session: Session = Depends(get_session)
) -> dict:
    auth.revoke_session(session, request.cookies.get(auth.SESSION_COOKIE))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"status": "signed_out"}


@router.get("/auth/session")
def current_session(
    request: Request,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_session_reviewer),
) -> dict:
    token = request.cookies.get(auth.SESSION_COOKIE)
    return {
        "reviewer": _serialize_reviewer(reviewer),
        "mustChangePassword": reviewer.must_change_password,
        "mfa": auth.mfa_status(session, reviewer, token),
        "expiresAt": _session_expiry(session, token),
    }


@router.get("/auth/mfa/status")
def mfa_status(
    request: Request,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_session_reviewer),
) -> dict:
    return auth.mfa_status(session, reviewer, request.cookies.get(auth.SESSION_COOKIE))


@router.post("/auth/mfa/enroll")
def start_mfa_enrollment(
    reviewer: m.Reviewer = Depends(require_reviewer),
) -> dict:
    return auth.start_mfa_enrollment(reviewer)


@router.post("/auth/mfa/enable")
def enable_mfa(
    body: MfaCodeRequest,
    request: Request,
    session: Session = Depends(get_unmanaged_session),
    reviewer: m.Reviewer = Depends(require_unmanaged_reviewer),
) -> dict:
    token = request.cookies.get(auth.SESSION_COOKIE)
    _commit_mfa_attempt(
        session,
        lambda: auth.enable_mfa(session, reviewer, token, body.code),
    )
    return auth.mfa_status(session, reviewer, token)


@router.post("/auth/mfa/verify")
def verify_mfa(
    body: MfaCodeRequest,
    request: Request,
    session: Session = Depends(get_unmanaged_session),
    reviewer: m.Reviewer = Depends(require_unmanaged_reviewer),
) -> dict:
    token = request.cookies.get(auth.SESSION_COOKIE)
    _commit_mfa_attempt(
        session,
        lambda: auth.verify_mfa_code(session, reviewer, token, body.code),
    )
    return auth.mfa_status(session, reviewer, token)


@router.post("/auth/change-password")
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_session_reviewer),
) -> dict:
    auth.change_password(
        session,
        reviewer,
        request.cookies.get(auth.SESSION_COOKIE),
        body.current_password,
        body.new_password,
    )
    return {"status": "changed"}


@router.get("/case")
def get_case(
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_reviewer),
) -> dict:
    return services.get_case_state(session, reviewer)


@router.get("/cases")
def list_cases(
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_reviewer),
) -> dict:
    return services.list_cases(session, reviewer)


@router.post("/cases", status_code=201)
def create_matter(
    body: NewMatterRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    """Open a new matter.

    Gated on the global account plane rather than a matter role, because a
    matter that does not exist yet has no membership to authorize against.
    """
    return services.create_matter_op(session, reviewer, body=body.model_dump(by_alias=True))


@router.get("/cases/{case_id}")
def get_case_by_id(
    case_id: str,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_reviewer),
) -> dict:
    return services.get_case_state(session, reviewer, case_id=case_id)


@router.post("/claims", status_code=201)
def create_claim(
    body: NewClaimRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_reviewer),
) -> dict:
    return services.create_claim_op(
        session,
        reviewer,
        source_document_id=body.source_document_id,
        text_=body.text,
        page_ref=body.page_ref,
        quote=body.quote,
        confidence=body.confidence,
        evidence_quality=body.evidence_quality,
    )


@router.post("/cases/{case_id}/claims", status_code=201)
def create_case_claim(
    case_id: str,
    body: NewClaimRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_reviewer),
) -> dict:
    return services.create_claim_op(
        session,
        reviewer,
        source_document_id=body.source_document_id,
        text_=body.text,
        page_ref=body.page_ref,
        quote=body.quote,
        confidence=body.confidence,
        evidence_quality=body.evidence_quality,
        case_id=case_id,
        source_page_extraction_id=body.source_page_extraction_id,
    )


@router.post("/cases/{case_id}/sources", status_code=201)
def create_case_source(case_id: str, body: NewSourceRequest, request: Request) -> dict:
    """Upload a document and extract its text.

    Unlike every other route, this one does **not** take a request-lifetime
    session. Extraction runs for as long as OCR takes — potentially minutes —
    and holding a database connection and an open transaction across that would
    exhaust the pool under a handful of concurrent uploads and hold row locks
    for no reason. So the work is split:

      1. a short transaction to authenticate, authorise, and validate;
      2. extraction with **no** session held;
      3. a fresh short transaction that re-authenticates from scratch and
         persists.

    Re-authenticating in phase 3 is deliberate: a session revoked or a
    membership withdrawn while OCR was running must stop the write, not be
    papered over by a decision made minutes earlier.
    """
    with short_request_session() as session:
        reviewer = require_mfa_reviewer(request, session)
        membership = services.authorize_case_action(session, reviewer, case_id)
        raw_bytes, prepared, replay = services.prepare_source_upload(
            session,
            reviewer,
            case_id=case_id,
            membership=membership,
            body=body.model_dump(by_alias=True),
        )
        if replay is not None:
            return replay

    extraction = ingestion.run_isolated_extraction(raw_bytes)

    with short_request_session() as session:
        reviewer = require_mfa_reviewer(request, session)
        services.authorize_case_action(session, reviewer, case_id)
        try:
            return services.create_source_op(
                session,
                reviewer,
                case_id=case_id,
                prepared=prepared,
                raw_bytes=raw_bytes,
                extraction=extraction,
            )
        except services._ConcurrentIdempotentUpload:
            pass

    # The losing side of a genuine race: another request with the same
    # idempotency key committed first. Return its result rather than an error.
    with short_request_session() as session:
        reviewer = require_mfa_reviewer(request, session)
        return services.resolve_idempotent_race(
            session, reviewer, case_id=case_id, prepared=prepared
        )


@router.get("/cases/{case_id}/sources/{source_id}/pages")
def list_case_source_pages(
    case_id: str,
    source_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_reviewer),
) -> dict:
    return services.list_source_pages_op(
        session,
        reviewer,
        case_id=case_id,
        source_id=source_id,
        offset=offset,
        limit=limit,
    )


@router.post("/cases/{case_id}/claims/{claim_id}/verify-quote")
def verify_case_claim_quote(
    case_id: str,
    claim_id: str,
    body: VerifyClaimQuoteRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.manually_verify_claim_quote_op(
        session,
        reviewer,
        case_id=case_id,
        claim_id=claim_id,
        reasoning=body.reasoning,
    )


@router.post("/conflicts", status_code=201)
def flag_conflict(
    body: FlagConflictRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_reviewer),
) -> dict:
    return services.flag_conflict_op(
        session,
        reviewer,
        claim_a_id=body.claim_a_id,
        claim_b_id=body.claim_b_id,
        conflict_type=body.conflict_type,
        severity=body.severity,
        summary=body.summary,
    )


@router.post("/cases/{case_id}/conflicts", status_code=201)
def flag_case_conflict(
    case_id: str,
    body: FlagConflictRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_reviewer),
) -> dict:
    return services.flag_conflict_op(
        session,
        reviewer,
        claim_a_id=body.claim_a_id,
        claim_b_id=body.claim_b_id,
        conflict_type=body.conflict_type,
        severity=body.severity,
        summary=body.summary,
        case_id=case_id,
    )


@router.post("/conflicts/{conflict_id}/decisions", status_code=201)
def apply_decision(
    conflict_id: str,
    body: DecisionRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.apply_decision_op(
        session,
        reviewer,
        conflict_id,
        decision_type=body.decision_type,
        selected_claim_id=body.selected_claim_id,
        reasoning=body.reasoning,
        expected_version=body.expected_version,
    )


@router.post("/cases/{case_id}/conflicts/{conflict_id}/decisions", status_code=201)
def apply_case_decision(
    case_id: str,
    conflict_id: str,
    body: DecisionRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.apply_decision_op(
        session,
        reviewer,
        conflict_id,
        decision_type=body.decision_type,
        selected_claim_id=body.selected_claim_id,
        reasoning=body.reasoning,
        expected_version=body.expected_version,
        case_id=case_id,
    )


@router.post("/report-sections", status_code=201)
def draft_section(
    body: SectionRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_reviewer),
) -> dict:
    return services.save_section_op(
        session,
        reviewer,
        title=body.title,
        paragraph_ref=body.paragraph_ref,
        text_=body.text,
        claim_ids=body.claim_ids,
        expected_version=body.expected_version,
    )


@router.post("/cases/{case_id}/report-sections", status_code=201)
def draft_case_section(
    case_id: str,
    body: SectionRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_reviewer),
) -> dict:
    return services.save_section_op(
        session,
        reviewer,
        title=body.title,
        paragraph_ref=body.paragraph_ref,
        text_=body.text,
        claim_ids=body.claim_ids,
        expected_version=body.expected_version,
        case_id=case_id,
    )


@router.put("/report-sections/{section_id}")
def revise_section(
    section_id: str,
    body: SectionUpdateRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_reviewer),
) -> dict:
    return services.save_section_op(
        session,
        reviewer,
        title=body.title,
        paragraph_ref=body.paragraph_ref,
        text_=body.text,
        claim_ids=body.claim_ids,
        section_id=section_id,
        expected_version=body.expected_version,
    )


@router.put("/cases/{case_id}/report-sections/{section_id}")
def revise_case_section(
    case_id: str,
    section_id: str,
    body: SectionUpdateRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_reviewer),
) -> dict:
    return services.save_section_op(
        session,
        reviewer,
        title=body.title,
        paragraph_ref=body.paragraph_ref,
        text_=body.text,
        claim_ids=body.claim_ids,
        section_id=section_id,
        expected_version=body.expected_version,
        case_id=case_id,
    )


@router.post("/report-sections/{section_id}/approve")
def approve_section(
    section_id: str,
    expected_version: int = Query(alias="expectedVersion", ge=1),
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.approve_section_op(
        session, reviewer, section_id, expected_version=expected_version
    )


@router.post("/cases/{case_id}/report-sections/{section_id}/approve")
def approve_case_section(
    case_id: str,
    section_id: str,
    expected_version: int = Query(alias="expectedVersion", ge=1),
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.approve_section_op(
        session,
        reviewer,
        section_id,
        expected_version=expected_version,
        case_id=case_id,
    )


@router.post("/packets", status_code=201)
def generate_packet(
    body: GeneratePacketRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.generate_packet_op(session, reviewer, packet_type=body.packet_type)


@router.post("/cases/{case_id}/packets", status_code=201)
def generate_case_packet(
    case_id: str,
    body: GeneratePacketRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.generate_packet_op(
        session, reviewer, packet_type=body.packet_type, case_id=case_id
    )


@router.get("/packets")
def list_packets(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.list_packet_artifacts(
        session,
        reviewer,
        limit=limit,
        offset=offset,
    )


@router.get("/cases/{case_id}/packets")
def list_case_packets(
    case_id: str,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.list_packet_artifacts(
        session,
        reviewer,
        case_id=case_id,
        limit=limit,
        offset=offset,
    )


@router.get("/packets/{packet_id}")
def get_packet(
    packet_id: str,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.get_packet_artifact(session, reviewer, packet_id=packet_id)


@router.get("/cases/{case_id}/packets/{packet_id}")
def get_case_packet(
    case_id: str,
    packet_id: str,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.get_packet_artifact(
        session,
        reviewer,
        packet_id=packet_id,
        case_id=case_id,
    )


@router.get("/audit/verify")
def verify_default_case_audit(
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.verify_case_integrity(session, reviewer)


@router.get("/cases/{case_id}/audit/verify")
def verify_case_audit(
    case_id: str,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.verify_case_integrity(session, reviewer, case_id=case_id)


@router.get("/packets/{packet_id}/verify")
def verify_packet(
    packet_id: str,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.verify_packet_integrity(
        session,
        reviewer,
        packet_id=packet_id,
    )


@router.get("/cases/{case_id}/packets/{packet_id}/verify")
def verify_case_packet(
    case_id: str,
    packet_id: str,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_reviewer),
) -> dict:
    return services.verify_packet_integrity(
        session,
        reviewer,
        packet_id=packet_id,
        case_id=case_id,
    )


@router.get("/admin/reviewers")
def list_reviewers(
    session: Session = Depends(get_session),
    _reviewer: m.Reviewer = Depends(require_mfa_admin_reviewer),
) -> dict:
    return services.list_reviewers_admin(session)


@router.get("/admin/account-audit")
def list_account_audit(
    session: Session = Depends(get_session),
    _reviewer: m.Reviewer = Depends(require_mfa_admin_reviewer),
) -> dict:
    return services.list_account_audit_admin(session)


@router.get("/admin/account-audit/verify")
def verify_account_audit(
    session: Session = Depends(get_session),
    _reviewer: m.Reviewer = Depends(require_mfa_admin_reviewer),
) -> dict:
    return services.verify_account_audit_integrity_admin(session)


@router.get("/admin/cases/{case_id}/members")
def list_case_members(
    case_id: str,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_admin_reviewer),
) -> dict:
    return services.list_case_members_admin(session, reviewer, case_id)


@router.post("/admin/cases/{case_id}/members", status_code=201)
def add_case_member(
    case_id: str,
    body: CaseMemberRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_admin_reviewer),
) -> dict:
    return services.set_case_member_admin(
        session,
        reviewer,
        case_id,
        reviewer_id=body.reviewer_id,
        role=body.role,
        is_active=True,
    )


@router.post("/admin/cases/{case_id}/members/{reviewer_id}/deactivate")
def deactivate_case_member(
    case_id: str,
    reviewer_id: str,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_admin_reviewer),
) -> dict:
    return services.deactivate_case_member_admin(session, reviewer, case_id, reviewer_id)


@router.post("/admin/cases/{case_id}/members/{reviewer_id}/reactivate")
def reactivate_case_member(
    case_id: str,
    reviewer_id: str,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_admin_reviewer),
) -> dict:
    return services.set_case_member_admin(
        session,
        reviewer,
        case_id,
        reviewer_id=reviewer_id,
        is_active=True,
    )


@router.post("/admin/reviewers", status_code=201)
def create_reviewer(
    body: CreateReviewerRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_admin_reviewer),
) -> dict:
    return services.create_reviewer_admin(
        session,
        reviewer,
        name=body.name,
        username=body.username,
        role=body.role,
        initial_password=body.initial_password,
    )


@router.post("/admin/reviewers/{reviewer_id}/deactivate")
def deactivate_reviewer(
    reviewer_id: str,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_admin_reviewer),
) -> dict:
    return services.set_reviewer_active_admin(session, reviewer, reviewer_id, is_active=False)


@router.post("/admin/reviewers/{reviewer_id}/reactivate")
def reactivate_reviewer(
    reviewer_id: str,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_admin_reviewer),
) -> dict:
    return services.set_reviewer_active_admin(session, reviewer, reviewer_id, is_active=True)


@router.post("/admin/reviewers/{reviewer_id}/reset-password")
def reset_reviewer_password(
    reviewer_id: str,
    body: ResetReviewerPasswordRequest,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_admin_reviewer),
) -> dict:
    return services.reset_reviewer_password_admin(
        session, reviewer, reviewer_id, new_password=body.new_password
    )


@router.post("/admin/reviewers/{reviewer_id}/reset-mfa")
def reset_reviewer_mfa(
    reviewer_id: str,
    session: Session = Depends(get_session),
    reviewer: m.Reviewer = Depends(require_mfa_admin_reviewer),
) -> dict:
    return services.reset_reviewer_mfa_admin(session, reviewer, reviewer_id)
