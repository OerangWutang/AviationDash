"""What is actually known about a claim's quote, and when that stops being true.

Two rules carry this module.

**An absence only counts when the text is complete.** A quote missing from a
clean, fully-extracted page is a real finding — the claim is rejected. A quote
missing from a page that failed, was truncated, or came from low-confidence OCR
proves nothing, so it routes to human attestation instead of either badge.

**Verification is bound to what was verified.** A "verified" claim whose quote,
source, or cited page later changes is no longer verified; keeping the badge
would make the label mean nothing precisely where it matters most.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..integrity import canonical_sha256
from . import types as t

#: Extraction states from which a *missing* quote is a confident absence.
#: Everything else is ambiguous and must not be treated as disproof.
_CONCLUSIVE_METHODS = frozenset({"native", "ocr"})


@dataclass(frozen=True)
class PageEvidence:
    """The immutable extracted page a quote is checked against."""

    id: str
    page_number: int
    extracted_text: str
    extraction_method: str
    ocr_confidence_bps: int | None
    text_truncated: bool

    @property
    def is_failed(self) -> bool:
        return self.extraction_method not in _CONCLUSIVE_METHODS

    def confidence_is_sufficient(self, minimum_percent: int) -> bool:
        """Native text has no confidence score — it is the document's own text
        layer, not an interpretation of pixels."""
        if self.extraction_method == "native":
            return True
        if self.ocr_confidence_bps is None:
            return False
        return self.ocr_confidence_bps >= minimum_percent * 100


def verification_basis_sha256(
    *,
    claim_id: str,
    source_document_id: str,
    source_page_extraction_id: str | None,
    normalized_quote: str,
) -> str:
    """Hash of exactly what a verification decision covered.

    ``page_ref`` is deliberately excluded: this feature proves a quote appears
    on a specific extracted page, not that the reviewer's citation label for
    that page is correct. Including it would invalidate real verifications for
    a cosmetic edit.
    """
    return canonical_sha256(
        {
            "schema": "atlas_argus.quote_verification_basis.v1",
            "claimId": claim_id,
            "sourceDocumentId": source_document_id,
            "sourcePageExtractionId": source_page_extraction_id,
            "quote": normalized_quote,
        }
    )


@dataclass(frozen=True)
class VerificationOutcome:
    state: str
    #: Set when the quote is confidently absent — the caller raises rather than
    #: storing a claim whose quote is not in the evidence.
    rejection: str | None = None

    @property
    def is_rejection(self) -> bool:
        return self.rejection is not None


def evaluate_quote(
    *,
    quote: str,
    page: PageEvidence | None,
    min_ocr_confidence_percent: int,
) -> VerificationOutcome:
    """Decide what is known about this quote against this page.

    Returns a state; never raises. A confident absence comes back as a
    rejection the caller turns into a 422, so the decision table stays in one
    readable place.
    """
    from ..ingestion import quote_appears_on_page

    if page is None:
        # Legacy or un-ingested source: nothing checked this, and the label
        # says so rather than implying a check occurred.
        return VerificationOutcome("legacy_unverified")

    if page.is_failed:
        # The reviewer is not blocked from citing the page they need; the claim
        # simply cannot reach a report without human attestation.
        return VerificationOutcome("manual_verification_required")

    found = quote_appears_on_page(quote, page.extracted_text)

    if found:
        if page.confidence_is_sufficient(min_ocr_confidence_percent):
            return VerificationOutcome("verified_exact")
        # A match inside text the OCR engine itself doubts is not strong
        # enough to badge as exact.
        return VerificationOutcome("manual_verification_required")

    if page.text_truncated:
        # Real text may continue past the cut, so absence here is unknown.
        return VerificationOutcome("manual_verification_required")

    return VerificationOutcome(
        "manual_verification_required",
        rejection=(
            f"That quote does not appear in the extracted text of page "
            f"{page.page_number}. Check the page reference, or correct the quote "
            f"to match the document."
        ),
    )


def basis_changed(
    *,
    previous_basis: str | None,
    new_basis: str,
) -> bool:
    return previous_basis != new_basis


def state_after_edit(
    *,
    previous_state: str,
    outcome: VerificationOutcome,
) -> str:
    """What a claim's verification becomes after its quote/source/page changed.

    A human attestation does not survive its own basis changing: someone
    confirmed *that* text on *that* page, and automatic matching on new text is
    not the same assurance. It drops back to requiring attestation, and the
    caller records an explicit invalidation event.
    """
    if previous_state == "manually_verified":
        return "manual_verification_required"
    return outcome.state


def blocks_report_eligibility(quote_verification: str) -> bool:
    """A claim whose quote nothing has confirmed must not reach a report,
    whatever its review status says."""
    return quote_verification in t.UNVERIFIED_QUOTE_STATES
