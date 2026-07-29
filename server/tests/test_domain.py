"""Pure domain doctrine — no database required (the harness reseeds anyway)."""

from __future__ import annotations

import pytest

from atlas_argus.domain.decisions import (
    transition_for,
    validate_decision,
)
from atlas_argus.domain.eligibility import compute_claim_eligibility
from atlas_argus.domain.permissions import decision_permission, packet_permission
from atlas_argus.domain.report import CitedClaim, compute_section_impact
from atlas_argus.domain.report_sections import revision_changes, validate_section


class TestEligibility:
    def test_privilege_wins_over_everything(self):
        assert (
            compute_claim_eligibility("rejected", "work_product", ["unresolved"])
            == "privileged"
        )

    def test_precedence_chain(self):
        assert compute_claim_eligibility("supported", "public", []) == "eligible"
        assert compute_claim_eligibility("rejected", "public", []) == "excluded"
        assert (
            compute_claim_eligibility("supported", "public", ["unresolved"])
            == "blocked_by_conflict"
        )
        assert compute_claim_eligibility("supported", "public", ["escalated"]) == "needs_review"
        assert compute_claim_eligibility("preserved", "confidential", ["preserved_both"]) == "eligible"
        assert compute_claim_eligibility("unreviewed", "public", []) == "needs_review"


class TestDecisions:
    def test_all_transitions(self):
        assert transition_for("accept_claim_a", None, "a").conflict_status == "accepted_claim_a"
        assert transition_for("accept_claim_a", None, "a").claim_b_status == "superseded"
        assert transition_for("accept_claim_b", None, "a").claim_a_status == "superseded"
        assert transition_for("preserve_both", None, "a").claim_a_status == "preserved"
        assert transition_for("mark_unresolved", None, "a").claim_b_status == "disputed"
        assert transition_for("escalate", None, "a").conflict_status == "escalated"

    def test_source_unreliable_does_not_auto_accept_survivor(self):
        transition = transition_for("mark_source_unreliable", "b", "a")
        assert transition.claim_b_status == "rejected"
        assert transition.claim_a_status == "unreviewed"

    def test_validation(self):
        assert "reasoning note" in validate_decision("escalate", None, "short").lower()
        assert "which claim" in validate_decision("mark_source_unreliable", None, "x" * 30).lower()
        assert validate_decision("escalate", None, "x" * 30) is None


class TestPermissions:
    def test_critical_dispositive_reserved_for_senior_roles(self):
        allowed, reason = decision_permission("accept_claim_a", "Claims Reviewer", "critical")
        assert not allowed
        assert "Senior Aviation Counsel" in reason
        assert decision_permission("escalate", "Claims Reviewer", "critical")[0]
        assert decision_permission("accept_claim_a", "Senior Aviation Counsel", "critical")[0]
        assert decision_permission("accept_claim_a", "Claims Reviewer", "high")[0]

    def test_production_packet_reserved_for_counsel(self):
        assert not packet_permission("production", "Accident Reconstruction Expert")[0]
        assert packet_permission("production", "Senior Aviation Counsel")[0]
        assert not packet_permission("internal", "Safety Investigator")[0]
        assert packet_permission("internal", "Senior Aviation Counsel")[0]


class TestSectionImpact:
    def test_blocked_names_the_conflict(self):
        impact = compute_section_impact(
            "PC-1",
            [
                CitedClaim("c1", "disputed", "blocked_by_conflict", ("stall timing dispute",)),
                CitedClaim("c2", "unreviewed", "privileged"),
            ],
        )
        assert impact.status == "blocked"
        assert "Report blocked" in impact.note
        assert "stall timing dispute" in impact.note

    def test_priority_and_disclosure(self):
        privileged = compute_section_impact(
            "X-1", [CitedClaim("c", "unreviewed", "privileged")]
        )
        assert privileged.status == "privileged_material"
        preserved = compute_section_impact(
            "X-1", [CitedClaim("c", "preserved", "eligible")]
        )
        assert preserved.status == "eligible_with_disclosure"
        eligible = compute_section_impact(
            "X-1", [CitedClaim("c", "supported", "eligible")]
        )
        assert eligible.status == "eligible"
        excluded = compute_section_impact(
            "X-1", [CitedClaim("c", "superseded", "excluded")]
        )
        assert excluded.status == "needs_review"
        assert "excluded" in excluded.note


class TestSectionValidation:
    OTHERS = [("rpt-pc", "PC-1", "Probable Cause")]
    KNOWN = {"clm-a4"}

    def test_rules(self):
        long_text = "x" * 50
        assert "title" in validate_section("ab", "Z-1", long_text, ["clm-a4"], self.OTHERS, self.KNOWN)
        assert "already used by “Probable Cause”" in validate_section(
            "Title", "pc-1", long_text, ["clm-a4"], self.OTHERS, self.KNOWN
        )
        assert "cite at least one claim" in validate_section(
            "Title", "Z-1", long_text, [], self.OTHERS, self.KNOWN
        )
        assert "not found: clm-x" in validate_section(
            "Title", "Z-1", long_text, ["clm-x"], self.OTHERS, self.KNOWN
        )
        # Editing may keep its own paragraph reference.
        assert (
            validate_section("Title", "PC-1", long_text, ["clm-a4"], self.OTHERS, self.KNOWN, "rpt-pc")
            is None
        )

    def test_revision_changes_diff(self):
        diff = revision_changes(
            "T", "R", "text", ["a"], "T", "R", "text2", ["a", "b"]
        )
        assert "citations added: b" in diff
        assert "text revised" in diff
        assert (
            revision_changes("T", "R", "x", ["a"], "T", "R", "x", ["a"])
            == "no substantive changes"
        )


class TestTransitionErrors:
    def test_unknown_decision_type_raises(self):
        from atlas_argus.domain.types import ValidationFailure

        with pytest.raises(ValidationFailure):
            transition_for("bogus", None, "a")
