import { describe, expect, it } from "vitest";
import type { Claim, Conflict } from "./types";
import {
  aggregateClaimStatus,
  applyDecision,
  validateDecision,
  type DecisionContext,
  type DecisionInput,
} from "./decisions";
import { computeClaimEligibility } from "./eligibility";

const NOW = "2026-07-09T12:00:00Z";

function makeClaim(overrides: Partial<Claim> & { id: string }): Claim {
  return {
    caseId: "case-1",
    text: "claim text",
    sourceDocumentId: "src-1",
    pageRef: "p. 1",
    quote: "quote",
    confidence: 0.8,
    evidenceQuality: "primary",
    status: "unreviewed",
    privilegeStatus: "public",
    reportEligibility: "blocked_by_conflict",
    reviewer: null,
    updatedAt: "2026-06-01T00:00:00Z",
    relatedConflictIds: ["cf-1"],
    ...overrides,
  };
}

function makeConflict(overrides: Partial<Conflict> = {}): Conflict {
  return {
    id: "cf-1",
    caseId: "case-1",
    claimAId: "claim-a",
    claimBId: "claim-b",
    conflictType: "factual",
    severity: "high",
    status: "unresolved",
    summary: "test conflict",
    createdAt: "2026-06-01T00:00:00Z",
    decisionIds: [],
    claimADisposition: "disputed",
    claimBDisposition: "disputed",
    ...overrides,
  };
}

function makeContext(
  conflictOverrides: Partial<Conflict> = {},
  claimAOverrides: Partial<Claim> = {},
  claimBOverrides: Partial<Claim> = {},
): DecisionContext {
  const conflict = makeConflict(conflictOverrides);
  const claimA = makeClaim({ id: "claim-a", ...claimAOverrides });
  const claimB = makeClaim({ id: "claim-b", ...claimBOverrides });
  let n = 0;
  return {
    conflict,
    claimA,
    claimB,
    conflictsById: new Map([[conflict.id, conflict]]),
    now: NOW,
    makeId: () => `id-${++n}`,
  };
}

function makeInput(overrides: Partial<DecisionInput> = {}): DecisionInput {
  return {
    conflictId: "cf-1",
    decisionType: "accept_claim_a",
    selectedClaimId: null,
    reasoning: "This reasoning note is definitely long enough to satisfy validation.",
    reviewerName: "Miriam Okafor",
    reviewerRole: "Senior Aviation Counsel",
    ...overrides,
  };
}

describe("validateDecision", () => {
  it("rejects reasoning shorter than the minimum", () => {
    expect(validateDecision(makeInput({ reasoning: "too short" }))).toMatch(
      /reasoning note/i,
    );
  });

  it("requires a selected claim for mark_source_unreliable", () => {
    expect(
      validateDecision(
        makeInput({ decisionType: "mark_source_unreliable", selectedClaimId: null }),
      ),
    ).toMatch(/which claim/i);
  });

  it("accepts a valid input", () => {
    expect(validateDecision(makeInput())).toBeNull();
  });
});

describe("applyDecision", () => {
  it("accept_claim_a: A supported+eligible, B superseded+excluded, nothing deleted", () => {
    const result = applyDecision(makeContext(), makeInput());
    expect(result.conflict.status).toBe("accepted_claim_a");
    const [a, b] = result.claims;
    expect(a.status).toBe("supported");
    expect(a.reportEligibility).toBe("eligible");
    expect(b.status).toBe("superseded");
    expect(b.reportEligibility).toBe("excluded");
    // The superseded claim keeps its full evidentiary content.
    expect(b.text).toBe("claim text");
    expect(b.quote).toBe("quote");
    expect(result.decision.previousStatus).toBe("unresolved");
    expect(result.decision.newStatus).toBe("accepted_claim_a");
    expect(result.conflict.decisionIds).toContain(result.decision.id);
  });

  it("accept_claim_b mirrors accept_claim_a", () => {
    const result = applyDecision(
      makeContext(),
      makeInput({ decisionType: "accept_claim_b" }),
    );
    expect(result.conflict.status).toBe("accepted_claim_b");
    expect(result.claims[0].status).toBe("superseded");
    expect(result.claims[1].status).toBe("supported");
    expect(result.decision.selectedClaimId).toBe("claim-b");
  });

  it("preserve_both: both preserved and eligible once the conflict is resolved", () => {
    const result = applyDecision(
      makeContext(),
      makeInput({ decisionType: "preserve_both" }),
    );
    expect(result.conflict.status).toBe("preserved_both");
    for (const claim of result.claims) {
      expect(claim.status).toBe("preserved");
      expect(claim.reportEligibility).toBe("eligible");
    }
  });

  it("mark_unresolved: claims disputed and blocked", () => {
    const result = applyDecision(
      makeContext({ status: "accepted_claim_a" }),
      makeInput({ decisionType: "mark_unresolved" }),
    );
    expect(result.conflict.status).toBe("unresolved");
    for (const claim of result.claims) {
      expect(claim.status).toBe("disputed");
      expect(claim.reportEligibility).toBe("blocked_by_conflict");
    }
  });

  it("escalate: claims escalated and parked as needs_review", () => {
    const result = applyDecision(makeContext(), makeInput({ decisionType: "escalate" }));
    expect(result.conflict.status).toBe("escalated");
    for (const claim of result.claims) {
      expect(claim.status).toBe("escalated");
      expect(claim.reportEligibility).toBe("needs_review");
    }
  });

  it("mark_source_unreliable: discredited claim rejected, survivor NOT auto-accepted", () => {
    const result = applyDecision(
      makeContext(),
      makeInput({ decisionType: "mark_source_unreliable", selectedClaimId: "claim-b" }),
    );
    expect(result.conflict.status).toBe("source_unreliable");
    const [a, b] = result.claims;
    expect(b.status).toBe("rejected");
    expect(b.reportEligibility).toBe("excluded");
    expect(a.status).toBe("unreviewed");
    expect(a.reportEligibility).toBe("needs_review");
    expect(result.decision.reportImpact).toMatch(/not auto-accepted/);
  });

  it("privilege withholds regardless of acceptance", () => {
    const result = applyDecision(
      makeContext({}, { privilegeStatus: "attorney_client" }),
      makeInput(),
    );
    expect(result.claims[0].status).toBe("supported");
    expect(result.claims[0].reportEligibility).toBe("privileged");
  });

  it("a claim in another still-unresolved conflict reads disputed, not accepted", () => {
    const other = makeConflict({ id: "cf-2", status: "unresolved" });
    const ctx = makeContext({}, { relatedConflictIds: ["cf-1", "cf-2"] });
    ctx.conflictsById = new Map([
      [ctx.conflict.id, ctx.conflict],
      [other.id, other],
    ]);
    const result = applyDecision(ctx, makeInput());
    // Accepting cf-1 does not clear the still-open cf-2: the global status is
    // the aggregate of both, and the open conflict's "disputed" outranks the
    // acceptance — not the misleading "supported" last-conflict-wins produced.
    expect(result.claims[0].status).toBe("disputed");
    expect(result.claims[0].reportEligibility).toBe("blocked_by_conflict");
  });

  it("emits an audit event for the conflict and each changed claim", () => {
    const result = applyDecision(makeContext(), makeInput());
    expect(result.auditEvents).toHaveLength(3);
    const [conflictEvent, ...claimEvents] = result.auditEvents;
    expect(conflictEvent.subjectType).toBe("conflict");
    expect(conflictEvent.detail).toBe(makeInput().reasoning);
    for (const event of claimEvents) {
      expect(event.subjectType).toBe("claim");
      expect(event.reportEligibilityChanged).toBe(true);
    }
  });

  it("throws on invalid input", () => {
    expect(() => applyDecision(makeContext(), makeInput({ reasoning: "x" }))).toThrow(
      /reasoning note/i,
    );
  });
});

describe("computeClaimEligibility", () => {
  it("orders privilege above everything else", () => {
    const claim = makeClaim({
      id: "c",
      status: "rejected",
      privilegeStatus: "work_product",
    });
    expect(computeClaimEligibility(claim, new Map())).toBe("privileged");
  });

  it("unreviewed claim without conflicts still needs review", () => {
    const claim = makeClaim({ id: "c", relatedConflictIds: [] });
    expect(computeClaimEligibility(claim, new Map())).toBe("needs_review");
  });
});

describe("aggregateClaimStatus", () => {
  it("is unreviewed with no dispositions", () => {
    expect(aggregateClaimStatus([])).toBe("unreviewed");
  });

  it("lets an out disposition dominate an open dispute and a positive one", () => {
    expect(aggregateClaimStatus(["disputed", "rejected", "supported"])).toBe("rejected");
    expect(aggregateClaimStatus(["superseded", "disputed", "preserved"])).toBe("superseded");
  });

  it("lets an open dispute dominate a positive disposition", () => {
    expect(aggregateClaimStatus(["supported", "disputed"])).toBe("disputed");
    expect(aggregateClaimStatus(["preserved", "escalated"])).toBe("escalated");
  });

  it("keeps preserved above supported so disclosure is never dropped", () => {
    expect(aggregateClaimStatus(["supported", "preserved"])).toBe("preserved");
  });

  it("returns a lone disposition unchanged", () => {
    for (const s of ["supported", "preserved", "disputed", "rejected"] as const) {
      expect(aggregateClaimStatus([s])).toBe(s);
    }
  });
});
