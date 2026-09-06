import type {
  AuditEvent,
  Claim,
  ClaimStatus,
  Conflict,
  ConflictStatus,
  DecisionType,
  ReviewDecision,
  ReviewerRole,
} from "./types";
import { computeClaimEligibility } from "./eligibility";

export const MIN_REASONING_LENGTH = 20;

/**
 * A claim's standing within one conflict, most-decisive first. A claim can be
 * in several conflicts at once; its global status is the most-decisive of its
 * per-conflict dispositions, never whichever conflict was touched last. Order
 * is doctrine and consistent with `computeClaimEligibility`'s rule order — an
 * "out" disposition (rejected/superseded) dominates an open dispute, and
 * `preserved` outranks `supported` so a preserved-conflict disclosure
 * obligation is never dropped when the claim is also accepted elsewhere.
 *
 * Mirrors `_CLAIM_STATUS_PRECEDENCE` / `aggregate_claim_status` in the Python
 * `domain/decisions.py`.
 */
const CLAIM_STATUS_PRECEDENCE: Record<ClaimStatus, number> = {
  rejected: 0,
  superseded: 1,
  disputed: 2,
  escalated: 3,
  preserved: 4,
  supported: 5,
  unreviewed: 6,
};

export function aggregateClaimStatus(dispositions: readonly ClaimStatus[]): ClaimStatus {
  if (dispositions.length === 0) {
    return "unreviewed";
  }
  return dispositions.reduce((best, d) =>
    CLAIM_STATUS_PRECEDENCE[d] < CLAIM_STATUS_PRECEDENCE[best] ? d : best,
  );
}

/** The disposition a conflict currently records for one of its two claims. */
function dispositionForClaim(conflict: Conflict, claimId: string): ClaimStatus {
  return claimId === conflict.claimAId
    ? conflict.claimADisposition
    : conflict.claimBDisposition;
}

/** Re-derive a claim's global status from every conflict it participates in. */
export function aggregateForClaim(
  claim: Pick<Claim, "id" | "relatedConflictIds">,
  conflictsById: ReadonlyMap<string, Conflict>,
): ClaimStatus {
  const dispositions = claim.relatedConflictIds
    .map((id) => conflictsById.get(id))
    .filter((c): c is Conflict => c !== undefined)
    .map((c) => dispositionForClaim(c, claim.id));
  return aggregateClaimStatus(dispositions);
}

export interface DecisionInput {
  conflictId: string;
  decisionType: DecisionType;
  /** Required for mark_source_unreliable: the claim rejected in this conflict. */
  selectedClaimId: string | null;
  reasoning: string;
  reviewerName: string;
  reviewerRole: ReviewerRole;
}

export interface DecisionContext {
  conflict: Conflict;
  claimA: Claim;
  claimB: Claim;
  /** All conflicts in the case, current state (used to recompute eligibility). */
  conflictsById: ReadonlyMap<string, Conflict>;
  now: string;
  makeId: () => string;
}

export interface DecisionResult {
  conflict: Conflict;
  claims: Claim[];
  decision: ReviewDecision;
  auditEvents: AuditEvent[];
}

/** Returns an error message if the input cannot be saved, else null. */
export function validateDecision(input: DecisionInput): string | null {
  if (input.reasoning.trim().length < MIN_REASONING_LENGTH) {
    return `A reasoning note of at least ${MIN_REASONING_LENGTH} characters is required before a decision can be saved.`;
  }
  if (input.decisionType === "mark_source_unreliable" && !input.selectedClaimId) {
    return "Select which claim’s source is unreliable before saving.";
  }
  return null;
}

interface Transition {
  conflictStatus: ConflictStatus;
  claimAStatus: ClaimStatus;
  claimBStatus: ClaimStatus;
}

function transitionFor(input: DecisionInput, conflict: Conflict): Transition {
  switch (input.decisionType) {
    case "accept_claim_a":
      return {
        conflictStatus: "accepted_claim_a",
        claimAStatus: "supported",
        claimBStatus: "superseded",
      };
    case "accept_claim_b":
      return {
        conflictStatus: "accepted_claim_b",
        claimAStatus: "superseded",
        claimBStatus: "supported",
      };
    case "preserve_both":
      return {
        conflictStatus: "preserved_both",
        claimAStatus: "preserved",
        claimBStatus: "preserved",
      };
    case "mark_unresolved":
      return {
        conflictStatus: "unresolved",
        claimAStatus: "disputed",
        claimBStatus: "disputed",
      };
    case "escalate":
      return {
        conflictStatus: "escalated",
        claimAStatus: "escalated",
        claimBStatus: "escalated",
      };
    case "mark_source_unreliable": {
      // The selected claim is rejected; the surviving claim is NOT
      // auto-accepted — rejecting one evidentiary position proves nothing about the
      // other claim, so it returns to the review queue.
      const aDiscredited = input.selectedClaimId === conflict.claimAId;
      return {
        conflictStatus: "source_unreliable",
        claimAStatus: aDiscredited ? "rejected" : "unreviewed",
        claimBStatus: aDiscredited ? "unreviewed" : "rejected",
      };
    }
  }
}

const DECISION_LABELS: Record<DecisionType, string> = {
  accept_claim_a: "Accept Claim A",
  accept_claim_b: "Accept Claim B",
  preserve_both: "Preserve both claims",
  mark_unresolved: "Mark unresolved",
  escalate: "Escalate for senior review",
  mark_source_unreliable: "Reject selected claim as unreliable",
};

export function decisionLabel(t: DecisionType): string {
  return DECISION_LABELS[t];
}

/**
 * Apply a review decision. Pure: returns updated copies of the conflict and
 * both claims, the decision record, and the audit events — the caller owns
 * persistence. Nothing is deleted; superseded/rejected claims keep their
 * text, source, and history.
 */
export function applyDecision(ctx: DecisionContext, input: DecisionInput): DecisionResult {
  const { conflict, claimA, claimB, now, makeId } = ctx;
  const validationError = validateDecision(input);
  if (validationError) {
    throw new Error(validationError);
  }

  const t = transitionFor(input, conflict);
  const previousStatus = conflict.status;

  // This conflict's verdict on each claim. The claim's global status is then
  // re-derived from this plus every other conflict it is in — deciding one
  // conflict no longer overwrites a disposition reached in another.
  const updatedConflict: Conflict = {
    ...conflict,
    status: t.conflictStatus,
    claimADisposition: t.claimAStatus,
    claimBDisposition: t.claimBStatus,
  };
  const conflictsById = new Map(ctx.conflictsById);
  conflictsById.set(updatedConflict.id, updatedConflict);

  const updateClaim = (claim: Claim): Claim => {
    const next: Claim = {
      ...claim,
      status: aggregateForClaim(claim, conflictsById),
      reviewer: input.reviewerName,
      updatedAt: now,
    };
    next.reportEligibility = computeClaimEligibility(next, conflictsById);
    return next;
  };

  const nextA = updateClaim(claimA);
  const nextB = updateClaim(claimB);

  const impactParts = [nextA, nextB].map((c, i) => {
    const label = i === 0 ? "Claim A" : "Claim B";
    return `${label} → ${c.reportEligibility.replace(/_/g, " ")}`;
  });
  if (input.decisionType === "mark_source_unreliable") {
    impactParts.push("surviving claim returns to review queue, not auto-accepted");
  }
  const reportImpact = impactParts.join("; ");

  const selectedClaimId =
    input.decisionType === "accept_claim_a"
      ? conflict.claimAId
      : input.decisionType === "accept_claim_b"
        ? conflict.claimBId
        : input.selectedClaimId;

  const decision: ReviewDecision = {
    id: makeId(),
    conflictId: conflict.id,
    decisionType: input.decisionType,
    selectedClaimId,
    reasoning: input.reasoning.trim(),
    reviewerName: input.reviewerName,
    reviewerRole: input.reviewerRole,
    createdAt: now,
    previousStatus,
    newStatus: t.conflictStatus,
    reportImpact,
  };

  const auditEvents: AuditEvent[] = [
    {
      id: makeId(),
      caseId: conflict.caseId,
      at: now,
      actor: input.reviewerName,
      actorRole: input.reviewerRole,
      action: `decision: ${DECISION_LABELS[input.decisionType]}`,
      subjectType: "conflict",
      subjectId: conflict.id,
      previousStatus,
      newStatus: t.conflictStatus,
      detail: decision.reasoning,
      reportEligibilityChanged: false,
    },
    ...[
      { prev: claimA, next: nextA },
      { prev: claimB, next: nextB },
    ]
      .filter(
        ({ prev, next }) =>
          prev.status !== next.status ||
          prev.reportEligibility !== next.reportEligibility,
      )
      .map(({ prev, next }) => ({
        id: makeId(),
        caseId: conflict.caseId,
        at: now,
        actor: input.reviewerName,
        actorRole: input.reviewerRole,
        action: "claim status updated",
        subjectType: "claim" as const,
        subjectId: next.id,
        previousStatus: prev.status,
        newStatus: next.status,
        detail: `Report eligibility: ${prev.reportEligibility} → ${next.reportEligibility}`,
        reportEligibilityChanged: prev.reportEligibility !== next.reportEligibility,
      })),
  ];

  return {
    conflict: { ...updatedConflict, decisionIds: [...conflict.decisionIds, decision.id] },
    claims: [nextA, nextB],
    decision,
    auditEvents,
  };
}
