"""The quote verification decision table, without a database.

The distinction these tests protect: a quote missing from *complete* text is a
finding worth rejecting a claim over; a quote missing from *incomplete* text is
merely unknown. Collapsing those two would either wave through fabrications or
block reviewers over failed OCR.
"""

from __future__ import annotations

import pytest

from atlas_argus.domain.quotes import (
    PageEvidence,
    blocks_report_eligibility,
    evaluate_quote,
    state_after_edit,
    verification_basis_sha256,
)

QUOTE = "the stick shaker activated at 2216:27"
PAGE_TEXT = (
    "The flight data recorder shows the stick shaker activated at 2216:27 "
    "with the airspeed decaying through 131 knots."
)


def _page(**overrides) -> PageEvidence:
    base = {
        "id": "pg-1",
        "page_number": 4,
        "extracted_text": PAGE_TEXT,
        "extraction_method": "native",
        "ocr_confidence_bps": None,
        "text_truncated": False,
    }
    base.update(overrides)
    return PageEvidence(**base)


def _evaluate(quote=QUOTE, page=None, minimum=70):
    return evaluate_quote(
        quote=quote,
        page=page if page is not None else _page(),
        min_ocr_confidence_percent=minimum,
    )


# ── the table ──────────────────────────────────────────────────────────────


def test_no_page_means_legacy_unverified_not_verified():
    outcome = evaluate_quote(quote=QUOTE, page=None, min_ocr_confidence_percent=70)
    assert outcome.state == "legacy_unverified"
    assert not outcome.is_rejection


def test_native_page_with_the_quote_is_verified_exact():
    outcome = _evaluate()
    assert outcome.state == "verified_exact"
    assert not outcome.is_rejection


def test_high_confidence_ocr_with_the_quote_is_verified_exact():
    outcome = _evaluate(page=_page(extraction_method="ocr", ocr_confidence_bps=8800))
    assert outcome.state == "verified_exact"


def test_low_confidence_ocr_match_needs_human_attestation():
    """A match inside text the engine itself doubts is not proof."""
    outcome = _evaluate(page=_page(extraction_method="ocr", ocr_confidence_bps=4200))
    assert outcome.state == "manual_verification_required"
    assert not outcome.is_rejection


def test_ocr_exactly_at_the_threshold_is_accepted():
    outcome = _evaluate(
        page=_page(extraction_method="ocr", ocr_confidence_bps=7000), minimum=70
    )
    assert outcome.state == "verified_exact"


def test_ocr_without_a_confidence_score_is_not_auto_verified():
    outcome = _evaluate(page=_page(extraction_method="ocr", ocr_confidence_bps=None))
    assert outcome.state == "manual_verification_required"


def test_failed_page_allows_the_claim_but_requires_attestation():
    """The reviewer is not blocked from citing the page they need."""
    outcome = _evaluate(page=_page(extraction_method="failed", extracted_text=""))
    assert outcome.state == "manual_verification_required"
    assert not outcome.is_rejection


def test_missing_quote_on_a_clean_page_is_rejected():
    """The actual fabrication check."""
    outcome = _evaluate(quote="the autopilot remained engaged throughout")
    assert outcome.is_rejection
    assert "does not appear" in outcome.rejection
    assert "page 4" in outcome.rejection


def test_missing_quote_on_truncated_text_is_unknown_not_rejected():
    """Real text may continue past the cut, so absence proves nothing."""
    outcome = _evaluate(quote="the autopilot remained engaged", page=_page(text_truncated=True))
    assert not outcome.is_rejection
    assert outcome.state == "manual_verification_required"


def test_quote_matching_survives_typographic_differences():
    outcome = _evaluate(quote="The  Stick   Shaker Activated at 2216:27")
    assert outcome.state == "verified_exact"


# ── basis and invalidation ─────────────────────────────────────────────────


def test_basis_changes_with_quote_source_or_page():
    base = dict(
        claim_id="clm-1",
        source_document_id="src-1",
        source_page_extraction_id="pg-1",
        normalized_quote="the stick shaker activated",
    )
    original = verification_basis_sha256(**base)
    assert verification_basis_sha256(**{**base, "normalized_quote": "something else"}) != original
    assert verification_basis_sha256(**{**base, "source_document_id": "src-2"}) != original
    assert verification_basis_sha256(**{**base, "source_page_extraction_id": "pg-2"}) != original
    assert verification_basis_sha256(**base) == original


def test_manual_attestation_does_not_survive_its_basis_changing():
    """Someone confirmed *that* text on *that* page. Automatic matching on new
    text is a different, weaker assurance."""
    outcome = _evaluate()
    assert outcome.state == "verified_exact"
    assert state_after_edit(previous_state="manually_verified", outcome=outcome) == (
        "manual_verification_required"
    )


def test_exact_verification_is_recomputed_on_edit():
    outcome = _evaluate()
    assert state_after_edit(previous_state="verified_exact", outcome=outcome) == "verified_exact"


def test_edit_onto_an_ambiguous_page_downgrades_an_exact_verification():
    outcome = _evaluate(page=_page(extraction_method="failed", extracted_text=""))
    assert state_after_edit(previous_state="verified_exact", outcome=outcome) == (
        "manual_verification_required"
    )


@pytest.mark.parametrize(
    ("state", "blocked"),
    [
        ("verified_exact", False),
        ("manually_verified", False),
        ("legacy_unverified", False),
        ("manual_verification_required", True),
    ],
)
def test_only_pending_attestation_blocks_report_eligibility(state, blocked):
    """Legacy claims are not blocked — that would retroactively break every
    existing matter. They are labelled honestly instead."""
    assert blocks_report_eligibility(state) is blocked
