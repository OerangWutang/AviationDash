import { describe, expect, it } from "vitest";
import type { Claim, Conflict, ReportSection } from "./types";
import { computeSectionImpact } from "./report";

function claim(id: string, overrides: Partial<Claim> = {}): Claim {
  return {
    id,
    caseId: "case-1",
    text: "t",
    sourceDocumentId: "src-1",
    pageRef: "p. 1",
    quote: "q",
    confidence: 0.8,
    evidenceQuality: "primary",
    status: "supported",
    privilegeStatus: "public",
    reportEligibility: "eligible",
    reviewer: null,
    updatedAt: "2026-06-01T00:00:00Z",
    relatedConflictIds: [],
    ...overrides,
  };
}

const section: ReportSection = {
  id: "rpt-1",
  caseId: "case-1",
  title: "Probable Cause",
  paragraphRef: "PC-1",
  text: "text [Source, p. 1]",
  claimIds: ["c1", "c2"],
};

function maps(claims: Claim[], conflicts: Conflict[] = []) {
  return {
    claimsById: new Map(claims.map((c) => [c.id, c])),
    conflictsById: new Map(conflicts.map((c) => [c.id, c])),
  };
}

describe("computeSectionImpact", () => {
  it("blocks when any cited claim is blocked by an unresolved conflict", () => {
    const { claimsById, conflictsById } = maps(
      [
        claim("c1", {
          reportEligibility: "blocked_by_conflict",
          relatedConflictIds: ["cf-1"],
        }),
        claim("c2", { reportEligibility: "privileged" }),
      ],
      [
        {
          id: "cf-1",
          caseId: "case-1",
          claimAId: "c1",
          claimBId: "cx",
          conflictType: "timeline",
          severity: "critical",
          status: "unresolved",
          claimADisposition: "disputed",
          claimBDisposition: "disputed",
          summary: "disputed stall-sequence claim",
          createdAt: "2026-06-01T00:00:00Z",
          decisionIds: [],
        },
      ],
    );
    const impact = computeSectionImpact(section, claimsById, conflictsById);
    expect(impact.status).toBe("blocked");
    expect(impact.note).toContain("Report blocked");
    expect(impact.note).toContain("disputed stall-sequence claim");
    expect(impact.offendingClaimIds).toEqual(["c1"]);
  });

  it("flags privileged material when nothing blocks", () => {
    const { claimsById, conflictsById } = maps([
      claim("c1"),
      claim("c2", { reportEligibility: "privileged" }),
    ]);
    const impact = computeSectionImpact(section, claimsById, conflictsById);
    expect(impact.status).toBe("privileged_material");
  });

  it("requires review when a cited claim was excluded", () => {
    const { claimsById, conflictsById } = maps([
      claim("c1"),
      claim("c2", { reportEligibility: "excluded", status: "superseded" }),
    ]);
    const impact = computeSectionImpact(section, claimsById, conflictsById);
    expect(impact.status).toBe("needs_review");
    expect(impact.note).toContain("excluded");
  });

  it("is eligible with disclosure when preserved claims are cited", () => {
    const { claimsById, conflictsById } = maps([
      claim("c1", { status: "preserved" }),
      claim("c2", { status: "preserved" }),
    ]);
    const impact = computeSectionImpact(section, claimsById, conflictsById);
    expect(impact.status).toBe("eligible_with_disclosure");
  });

  it("is eligible when all cited claims are eligible", () => {
    const { claimsById, conflictsById } = maps([claim("c1"), claim("c2")]);
    const impact = computeSectionImpact(section, claimsById, conflictsById);
    expect(impact.status).toBe("eligible");
  });
});
