import type { Conflict, DecisionType, ReviewerRole } from "./types";

export interface DecisionPermission {
  allowed: boolean;
  /** Shown in the UI next to a disabled option — never a silently dead control. */
  reason: string | null;
}

const DISPOSITIVE: readonly DecisionType[] = [
  "accept_claim_a",
  "accept_claim_b",
  "mark_source_unreliable",
];

const SENIOR_ROLES: readonly ReviewerRole[] = [
  "Senior Aviation Counsel",
  "Accident Reconstruction Expert",
];

/**
 * Who may take which decision.
 *
 * Dispositive decisions (accepting a claim, discrediting a source) on
 * critical-severity conflicts are reserved for senior reviewers; everyone
 * can preserve, escalate, or mark unresolved — those never destroy the
 * evidentiary position.
 */
export function decisionPermission(
  decisionType: DecisionType,
  role: ReviewerRole,
  conflict: Pick<Conflict, "severity">,
): DecisionPermission {
  if (
    conflict.severity === "critical" &&
    DISPOSITIVE.includes(decisionType) &&
    !SENIOR_ROLES.includes(role)
  ) {
    return {
      allowed: false,
      reason:
        "Critical-severity conflicts require Senior Aviation Counsel or an Accident Reconstruction Expert to accept a claim or discredit a source. Use “Escalate for senior review”.",
    };
  }
  return { allowed: true, reason: null };
}
