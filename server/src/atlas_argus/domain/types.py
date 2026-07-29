"""Atlas Argus domain vocabulary.

Mirrors ``src/domain/types.ts`` in the frontend — the two must stay in
lockstep. Core doctrine: every important fact traces claim → source →
conflict → review decision → report impact, and nothing is ever silently
deleted; history is append-only.
"""

from __future__ import annotations

from typing import Final

CLAIM_STATUSES: Final = frozenset(
    {"unreviewed", "supported", "disputed", "superseded", "rejected", "preserved", "escalated"}
)

CONFLICT_STATUSES: Final = frozenset(
    {
        "unresolved",
        "accepted_claim_a",
        "accepted_claim_b",
        "preserved_both",
        "escalated",
        "source_unreliable",
        "closed",
    }
)

REPORT_ELIGIBILITIES: Final = frozenset(
    {"eligible", "blocked_by_conflict", "privileged", "needs_review", "excluded"}
)

PRIVILEGE_STATUSES: Final = frozenset(
    {"public", "confidential", "attorney_client", "work_product", "restricted"}
)

#: Privilege statuses that withhold material from report use entirely.
WITHHOLDING_PRIVILEGE: Final = frozenset({"attorney_client", "work_product", "restricted"})

EVIDENCE_QUALITIES: Final = frozenset(
    {"primary", "corroborated", "secondary", "uncorroborated"}
)

SOURCE_TYPES: Final = frozenset(
    {
        "flight_recorder",
        "official_report",
        "deposition",
        "expert_report",
        "operational_record",
        "atc_transcript",
        "weather_data",
        "internal_memo",
    }
)

CONFLICT_TYPES: Final = frozenset(
    {"timeline", "factual", "interpretation", "source_reliability"}
)

CONFLICT_SEVERITIES: Final = frozenset({"critical", "high", "medium", "low"})

DECISION_TYPES: Final = frozenset(
    {
        "accept_claim_a",
        "accept_claim_b",
        "preserve_both",
        "mark_unresolved",
        "escalate",
        "mark_source_unreliable",
    }
)

REVIEWER_ROLES: Final = frozenset(
    {
        "Senior Aviation Counsel",
        "Accident Reconstruction Expert",
        "Claims Reviewer",
        "Safety Investigator",
    }
)

AUDIT_SUBJECT_TYPES: Final = frozenset(
    {"claim", "conflict", "source", "report_section", "export", "reviewer"}
)

#: How a page's text was obtained. ``failed`` is a first-class outcome, not an
#: absence: a page that could not be extracted is still citable, but every
#: quote against it needs human attestation rather than an automatic match.
EXTRACTION_METHODS: Final = frozenset({"native", "ocr", "failed"})

#: Why a page has no usable text. Bounded so the value can be shown to a
#: reviewer and hashed into the extraction manifest; raw exception text never
#: reaches either.
EXTRACTION_FAILURE_CODES: Final = frozenset(
    {
        "native_extract_error",
        "invalid_media_box",
        "pixel_limit",
        "raster_timeout",
        "ocr_timeout",
        "ocr_unavailable",
        "page_parse_error",
        "total_text_budget_exhausted",
    }
)

#: What is actually known about a claim's quote.
#:
#: - ``verified_exact``: the quote appears in this page's extracted text.
#: - ``manual_verification_required``: extraction can neither confirm nor
#:   confidently deny it (failed page, truncated text, low-confidence OCR).
#: - ``manually_verified``: a second, privilege-cleared reviewer attested to it
#:   against the original document.
#: - ``legacy_unverified``: the source predates ingestion; the honest label for
#:   "nothing checked this", never presented as verified.
QUOTE_VERIFICATIONS: Final = frozenset(
    {"verified_exact", "manual_verification_required", "manually_verified", "legacy_unverified"}
)

#: Verification states that must not reach a production report on their own.
UNVERIFIED_QUOTE_STATES: Final = frozenset({"manual_verification_required"})

QUOTE_VERIFICATION_EVENTS: Final = frozenset({"manual_verified", "verification_invalidated"})

SECTION_STATUSES: Final = frozenset(
    {"eligible", "eligible_with_disclosure", "blocked", "privileged_material", "needs_review"}
)

DECISION_LABELS: Final[dict[str, str]] = {
    "accept_claim_a": "Accept Claim A",
    "accept_claim_b": "Accept Claim B",
    "preserve_both": "Preserve both claims",
    "mark_unresolved": "Mark unresolved",
    "escalate": "Escalate for senior review",
    "mark_source_unreliable": "Mark source unreliable",
}


class DomainError(Exception):
    """Base for errors the API maps to HTTP responses."""

    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationFailure(DomainError):
    status_code = 422


class Unauthorized(DomainError):
    status_code = 401


class RateLimited(DomainError):
    status_code = 429


class PermissionDenied(DomainError):
    status_code = 403


class NotFound(DomainError):
    status_code = 404


class DuplicateConflict(DomainError):
    status_code = 409


class StaleUpdate(DomainError):
    status_code = 409
