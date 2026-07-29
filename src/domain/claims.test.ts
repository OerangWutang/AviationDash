import { describe, expect, it } from "vitest";
import {
  createClaim,
  flagConflict,
  normalizeQuoteText,
  quoteAppearsOnPage,
  validateFlagConflict,
  validateNewClaim,
  type FlagConflictContext,
  type NewClaimInput,
} from "./claims";
import * as sample from "../data/sampleCase";

const NOW = "2026-07-10T12:00:00Z";

function newClaimInput(overrides: Partial<NewClaimInput> = {}): NewClaimInput {
  return {
    sourceDocumentId: "src-fdr",
    text: "Engine torque remained symmetrical through the upset sequence.",
    pageRef: "p. 51, parameter plot 17",
    quote: "Torque L/R traces track within 2% through 22:16:40.",
    confidence: 0.9,
    evidenceQuality: "primary",
    ...overrides,
  };
}

const claimsById = new Map(sample.claims.map((c) => [c.id, c]));
const conflictsById = new Map(sample.conflicts.map((c) => [c.id, c]));

function sourceById(id: string) {
  const source = sample.sources.find((s) => s.id === id);
  if (!source) throw new Error(`missing source ${id}`);
  return source;
}

describe("validateNewClaim", () => {
  it("rejects short text, missing page ref, short quote, bad confidence", () => {
    expect(validateNewClaim(newClaimInput({ text: "too short" }))).toMatch(/Claim text/);
    expect(validateNewClaim(newClaimInput({ pageRef: "  " }))).toMatch(/page\/section/i);
    expect(validateNewClaim(newClaimInput({ quote: "short" }))).toMatch(/quote/i);
    expect(validateNewClaim(newClaimInput({ confidence: 1.2 }))).toMatch(/Confidence/);
    expect(validateNewClaim(newClaimInput())).toBeNull();
  });
});

describe("createClaim", () => {
  it("creates an unreviewed claim that inherits the source's privilege", () => {
    const { claim, auditEvent } = createClaim(
      {
        source: sourceById("src-memo"),
        claimId: "clm-11",
        eventId: "ev-1",
        now: NOW,
        actorName: "Priya Natarajan",
        actorRole: "Claims Reviewer",
      },
      newClaimInput({ sourceDocumentId: "src-memo" }),
    );
    expect(claim.status).toBe("unreviewed");
    expect(claim.privilegeStatus).toBe("attorney_client");
    expect(claim.reportEligibility).toBe("privileged");
    expect(claim.relatedConflictIds).toEqual([]);
    expect(auditEvent.action).toBe("claim extracted");
    expect(auditEvent.detail).toContain("Privilege inherited from source: attorney_client");
  });

  it("public source → claim needs review until affirmatively supported", () => {
    const { claim } = createClaim(
      {
        source: sourceById("src-fdr"),
        claimId: "clm-11",
        eventId: "ev-1",
        now: NOW,
        actorName: "Priya Natarajan",
        actorRole: "Claims Reviewer",
      },
      newClaimInput(),
    );
    expect(claim.privilegeStatus).toBe("public");
    expect(claim.reportEligibility).toBe("needs_review");
  });

  it("throws on invalid input", () => {
    expect(() =>
      createClaim(
        {
          source: sourceById("src-fdr"),
          claimId: "clm-11",
          eventId: "ev-1",
          now: NOW,
          actorName: "Priya Natarajan",
          actorRole: "Claims Reviewer",
        },
        newClaimInput({ quote: "x" }),
      ),
    ).toThrow(/quote/i);
  });
});

describe("validateFlagConflict", () => {
  const input = {
    claimAId: "clm-a4",
    claimBId: "clm-b2",
    conflictType: "factual" as const,
    severity: "medium" as const,
    summary: "Sample summary long enough to pass validation.",
  };

  it("rejects identical claims, missing claims, and short summaries", () => {
    expect(
      validateFlagConflict({ ...input, claimBId: "clm-a4" }, claimsById, conflictsById),
    ).toMatch(/two different claims/i);
    expect(
      validateFlagConflict({ ...input, claimBId: "clm-nope" }, claimsById, conflictsById),
    ).toMatch(/must exist/i);
    expect(
      validateFlagConflict({ ...input, summary: "short" }, claimsById, conflictsById),
    ).toMatch(/summary/i);
  });

  it("rejects a duplicate open conflict for the same pair, in either order", () => {
    expect(
      validateFlagConflict(
        { ...input, claimAId: "clm-a1", claimBId: "clm-b1" },
        claimsById,
        conflictsById,
      ),
    ).toMatch(/CF-1/);
    expect(
      validateFlagConflict(
        { ...input, claimAId: "clm-b1", claimBId: "clm-a1" },
        claimsById,
        conflictsById,
      ),
    ).toMatch(/CF-1/);
  });

  it("allows re-flagging a pair whose prior conflict is resolved", () => {
    // cf-4 (clm-a4 / clm-b4) is accepted_claim_a — not open.
    expect(
      validateFlagConflict(
        { ...input, claimAId: "clm-a4", claimBId: "clm-b4" },
        claimsById,
        conflictsById,
      ),
    ).toBeNull();
  });
});

describe("flagConflict", () => {
  function ctx(): FlagConflictContext {
    let n = 0;
    return {
      claimsById,
      conflictsById,
      conflictId: "cf-6",
      now: NOW,
      makeEventId: () => `ev-${++n}`,
      actorName: "Daniel Reeves",
      actorRole: "Accident Reconstruction Expert",
    };
  }

  it("creates an unresolved conflict and disputes+blocks both claims", () => {
    const result = flagConflict(ctx(), {
      claimAId: "clm-a4",
      claimBId: "clm-b2",
      conflictType: "interpretation",
      severity: "high",
      summary: "Autopilot findings conflict with icing-performance analysis.",
    });
    expect(result.conflict.status).toBe("unresolved");
    expect(result.conflict.id).toBe("cf-6");
    for (const claim of result.claims) {
      expect(claim.status).toBe("disputed");
      expect(claim.reportEligibility).toBe("blocked_by_conflict");
      expect(claim.relatedConflictIds).toContain("cf-6");
    }
    // Prior conflict memberships are preserved, not replaced.
    expect(result.claims[0].relatedConflictIds).toEqual(["cf-4", "cf-6"]);
    expect(result.auditEvents).toHaveLength(3);
    expect(result.auditEvents[0].action).toBe("conflict flagged");
    // clm-a4 was eligible before — flagging changed its report eligibility.
    expect(result.auditEvents[1].reportEligibilityChanged).toBe(true);
  });

  it("privilege still wins over the dispute state", () => {
    const result = flagConflict(ctx(), {
      claimAId: "clm-a5",
      claimBId: "clm-b2",
      conflictType: "interpretation",
      severity: "medium",
      summary: "Privileged training review conflicts with icing finding.",
    });
    const privileged = result.claims.find((c) => c.id === "clm-a5")!;
    expect(privileged.status).toBe("disputed");
    expect(privileged.reportEligibility).toBe("privileged");
  });

  it("throws on validation failure", () => {
    expect(() =>
      flagConflict(ctx(), {
        claimAId: "clm-a1",
        claimBId: "clm-b1",
        conflictType: "timeline",
        severity: "high",
        summary: "Duplicate of the existing open conflict CF-1.",
      }),
    ).toThrow(/CF-1/);
  });
});

describe("quote normalization (mirrors the Python extractor)", () => {
  // These cases are duplicated verbatim in
  // server/tests/test_ingestion.py::test_normalisation_folds_typography_not_substance.
  // The two implementations decide the same question — this one drives the
  // editor's hint, the Python one decides whether the server accepts the
  // claim — so a divergence would have the UI promise matches the server
  // rejects. Keep both lists in step.
  it.each([
    ["The  quick   brown", "the quick brown"],
    ["“curly quotes”", '"curly quotes"'],
    ["em—dash", "em-dash"],
    ["soft­hyphen", "softhyphen"],
    ["line-\nwrapped", "linewrapped"],
    ["  Leading and trailing  ", "leading and trailing"],
  ])("normalizes %j", (raw, expected) => {
    expect(normalizeQuoteText(raw)).toBe(expected);
  });

  it("matches a quote across a PDF line-wrap hyphen", () => {
    expect(
      quoteAppearsOnPage(
        "airspeed was decaying through 131 knots",
        "the airspeed was decay-\ning through 131 knots",
      ),
    ).toBe(true);
  });

  it("does not match an absent quote", () => {
    expect(
      quoteAppearsOnPage("the autopilot was engaged", "the stick shaker activated"),
    ).toBe(false);
  });

  it("never matches an empty quote", () => {
    expect(quoteAppearsOnPage("", "any page text")).toBe(false);
    expect(quoteAppearsOnPage("   ", "any page text")).toBe(false);
  });
});
