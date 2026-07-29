"""Role gating. Mirrors ``src/domain/permissions.ts`` and ``packet.ts``.

Dispositive decisions (accepting a claim, discrediting a source) on
critical-severity conflicts are reserved for senior reviewers; everyone can
preserve, escalate, or mark unresolved — those never destroy the evidentiary
position. Producing an external evidence packet is a legal act reserved for
counsel.
"""

from __future__ import annotations

DISPOSITIVE_DECISIONS = frozenset(
    {"accept_claim_a", "accept_claim_b", "mark_source_unreliable"}
)

SENIOR_ROLES = frozenset({"Senior Aviation Counsel", "Accident Reconstruction Expert"})

CRITICAL_DECISION_REASON = (
    "Critical-severity conflicts require Senior Aviation Counsel or an Accident "
    "Reconstruction Expert to accept a claim or discredit a source. "
    "Use “Escalate for senior review”."
)

PACKET_REASON = "Evidence packet generation is reserved for Senior Aviation Counsel."

ADMIN_REASON = "Reviewer administration is reserved for Senior Aviation Counsel."


def decision_permission(
    decision_type: str, role: str, conflict_severity: str
) -> tuple[bool, str | None]:
    if (
        conflict_severity == "critical"
        and decision_type in DISPOSITIVE_DECISIONS
        and role not in SENIOR_ROLES
    ):
        return False, CRITICAL_DECISION_REASON
    return True, None


def packet_permission(packet_type: str, role: str) -> tuple[bool, str | None]:
    if packet_type in {"internal", "production"} and role != "Senior Aviation Counsel":
        return False, PACKET_REASON
    return True, None


def admin_permission(role: str) -> tuple[bool, str | None]:
    if role != "Senior Aviation Counsel":
        return False, ADMIN_REASON
    return True, None
