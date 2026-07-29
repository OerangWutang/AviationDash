"""Review-decision transitions. Mirrors ``src/domain/decisions.ts``.

Pure: given a decision input and the conflict's current state, produce the
new conflict status and both claims' new statuses. Nothing is deleted;
superseded/rejected claims keep their text, source, and history.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .types import ValidationFailure

MIN_REASONING_LENGTH = 20

# A claim's standing *within one conflict*, most-decisive first. A claim can sit
# in several conflicts at once; its global status is the most-decisive of its
# per-conflict dispositions, never whichever conflict was touched last. Order is
# doctrine (confirmed with counsel) and consistent with ``compute_claim_
# eligibility``'s rule order — "out" (rejected/superseded) dominates an open
# dispute, and ``preserved`` outranks ``supported`` so a preserved-conflict
# disclosure obligation is never dropped when the claim is also accepted
# elsewhere.
_CLAIM_STATUS_PRECEDENCE: dict[str, int] = {
    "rejected": 0,
    "superseded": 1,
    "disputed": 2,
    "escalated": 3,
    "preserved": 4,
    "supported": 5,
    "unreviewed": 6,
}


def aggregate_claim_status(dispositions: Iterable[str]) -> str:
    """Collapse a claim's per-conflict dispositions into one global status.

    Empty (a claim in no conflict) is ``unreviewed``. Mirrors
    ``aggregateClaimStatus`` in ``src/domain/decisions.ts``.
    """
    ranked = [d for d in dispositions if d in _CLAIM_STATUS_PRECEDENCE]
    if not ranked:
        return "unreviewed"
    return min(ranked, key=lambda d: _CLAIM_STATUS_PRECEDENCE[d])


@dataclass(frozen=True)
class Transition:
    conflict_status: str
    claim_a_status: str
    claim_b_status: str


def validate_decision(
    decision_type: str, selected_claim_id: str | None, reasoning: str
) -> str | None:
    """Return an error message if the input cannot be saved, else None."""
    if len(reasoning.strip()) < MIN_REASONING_LENGTH:
        return (
            f"A reasoning note of at least {MIN_REASONING_LENGTH} characters is "
            "required before a decision can be saved."
        )
    if decision_type == "mark_source_unreliable" and not selected_claim_id:
        return "Select which claim’s source is unreliable before saving."
    return None


def transition_for(
    decision_type: str, selected_claim_id: str | None, claim_a_id: str
) -> Transition:
    match decision_type:
        case "accept_claim_a":
            return Transition("accepted_claim_a", "supported", "superseded")
        case "accept_claim_b":
            return Transition("accepted_claim_b", "superseded", "supported")
        case "preserve_both":
            return Transition("preserved_both", "preserved", "preserved")
        case "mark_unresolved":
            return Transition("unresolved", "disputed", "disputed")
        case "escalate":
            return Transition("escalated", "escalated", "escalated")
        case "mark_source_unreliable":
            # The discredited claim is rejected; the surviving claim is NOT
            # auto-accepted — discrediting one source proves nothing about
            # the other claim, so it returns to the review queue.
            a_discredited = selected_claim_id == claim_a_id
            return Transition(
                "source_unreliable",
                "rejected" if a_discredited else "unreviewed",
                "unreviewed" if a_discredited else "rejected",
            )
        case _:
            raise ValidationFailure(f"Unknown decision type: {decision_type}.")


def report_impact_summary(
    eligibility_a: str, eligibility_b: str, decision_type: str
) -> str:
    parts = [
        f"Claim A → {eligibility_a.replace('_', ' ')}",
        f"Claim B → {eligibility_b.replace('_', ' ')}",
    ]
    if decision_type == "mark_source_unreliable":
        parts.append("surviving claim returns to review queue, not auto-accepted")
    return "; ".join(parts)
