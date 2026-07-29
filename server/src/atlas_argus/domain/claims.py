"""Claim-extraction and conflict-flagging validation.

Mirrors ``src/domain/claims.ts``:
- A claim is born from a source document and inherits its privilege
  designation — never chosen at extraction.
- Every claim must carry a page reference and a supporting quote.
- Flagging a conflict disputes both claims and blocks their report use;
  the same pair cannot carry two open conflicts.
"""

from __future__ import annotations

MIN_CLAIM_TEXT_LENGTH = 15
MIN_QUOTE_LENGTH = 10
MIN_SUMMARY_LENGTH = 15

OPEN_CONFLICT_STATUSES = frozenset({"unresolved", "escalated"})


def validate_new_claim(
    text: str, page_ref: str, quote: str, confidence: float
) -> str | None:
    if len(text.strip()) < MIN_CLAIM_TEXT_LENGTH:
        return f"Claim text of at least {MIN_CLAIM_TEXT_LENGTH} characters is required."
    if len(page_ref.strip()) == 0:
        return (
            "A page/section reference is required — every claim must trace to a "
            "source location."
        )
    if len(quote.strip()) < MIN_QUOTE_LENGTH:
        return f"A supporting quote of at least {MIN_QUOTE_LENGTH} characters is required."
    if not (0 <= confidence <= 1):
        return "Confidence must be between 0 and 100%."
    return None


def validate_flag_conflict(claim_a_id: str, claim_b_id: str, summary: str) -> str | None:
    """Structural checks; existence and duplicate checks live in the service."""
    if claim_a_id == claim_b_id:
        return "Select two different claims."
    if len(summary.strip()) < MIN_SUMMARY_LENGTH:
        return (
            f"A conflict summary of at least {MIN_SUMMARY_LENGTH} characters is required."
        )
    return None
