import { describe, expect, it } from "vitest";
import type { Claim, Conflict } from "./types";
import {
  buildEvidencePacket,
  escapeHtml,
  packetFilename,
  packetPermission,
  renderPacketBody,
  renderPacketDocument,
  sha256Hex,
  type PacketBuildInput,
  type PacketMeta,
} from "./packet";
import { applyDecision } from "./decisions";
import * as sample from "../data/sampleCase";

function meta(type: PacketMeta["type"]): PacketMeta {
  return {
    packetId: "pkt-test-1",
    type,
    generatedAt: "2026-07-09T21:00:00Z",
    generatedByName: "Miriam Okafor",
    generatedByRole: "Senior Aviation Counsel",
  };
}

function sampleInput(type: PacketMeta["type"]): PacketBuildInput {
  return {
    caseFile: sample.caseFile,
    sections: sample.reportSections,
    claimsById: new Map(sample.claims.map((c) => [c.id, c])),
    conflictsById: new Map(sample.conflicts.map((c) => [c.id, c])),
    sourcesById: new Map(sample.sources.map((s) => [s.id, s])),
    decisions: sample.decisions,
    meta: meta(type),
  };
}

// Text that only exists in privileged material / internal assessments.
const MEMO_QUOTE_FRAGMENT = "materially thinner";
const MEMO_CLAIM_FRAGMENT = "stall-recovery and tailplane-icing training";
const DECISION_REASONING_FRAGMENT = "dispositive on autopilot state";

describe("packetPermission", () => {
  it("reserves evidence packets for Senior Aviation Counsel", () => {
    expect(packetPermission("production", "Claims Reviewer").allowed).toBe(false);
    expect(packetPermission("production", "Accident Reconstruction Expert").allowed).toBe(
      false,
    );
    expect(packetPermission("internal", "Claims Reviewer").allowed).toBe(false);
    expect(packetPermission("internal", "Safety Investigator").allowed).toBe(false);
    expect(packetPermission("production", "Senior Aviation Counsel").allowed).toBe(true);
    expect(packetPermission("internal", "Senior Aviation Counsel").allowed).toBe(true);
  });
});

describe("buildEvidencePacket — production", () => {
  const packet = buildEvidencePacket(sampleInput("production"));

  it("includes only eligible sections; excludes blocked; withholds privileged", () => {
    const included = packet.entries.find((entry) => entry.disposition === "included");
    expect(included?.section.paragraphRef).toBe("ACFT-1");
    expect(packet.stats).toEqual({ included: 1, excluded: 4, withheld: 1 });

    const nonIncluded = packet.entries.filter((entry) => entry.disposition !== "included");
    expect(nonIncluded).toHaveLength(5);
    for (const entry of nonIncluded) {
      const prefix = entry.disposition === "withheld" ? "WITHHELD" : "EXCLUDED";
      expect(entry.section.id).toMatch(new RegExp(`^${prefix}-\\d{3}$`));
      expect(entry.section.paragraphRef).toBe(entry.section.id);
      expect(entry.section.title).toBe(
        entry.disposition === "withheld" ? "Withheld material" : "Excluded material",
      );
      expect(entry.section.text).toBe("");
      expect(entry.section.claimIds).toEqual([]);
      expect(entry.impact.offendingClaimIds).toEqual([]);
      expect(entry.reason).toBe(
        entry.disposition === "withheld"
          ? "Withheld under privilege; see privilege log."
          : "Excluded from production.",
      );
    }
    const serialized = JSON.stringify(nonIncluded);
    for (const sentinel of ["PC-1", "WX-2", "TRN-2", "clm-a5", "rpt-trn"]) {
      expect(serialized).not.toContain(sentinel);
    }
  });

  it("builds a privilege log by identity only", () => {
    expect(packet.privilegeLog).toHaveLength(1);
    const entry = packet.privilegeLog[0];
    expect(entry.source.id).toBe("src-memo");
    expect(entry.sectionRefs).toEqual(["WITHHELD-001"]);
  });

  it("strips the decision log entirely (work product)", () => {
    expect(packet.decisionLog).toBeNull();
  });

  it("never leaks privileged content or internal assessments into the body", () => {
    const body = renderPacketBody(packet);
    expect(body).toContain("Privilege log");
    expect(body).toContain("Internal Litigation Strategy Memorandum");
    expect(body).not.toContain(MEMO_QUOTE_FRAGMENT);
    expect(body).not.toContain(MEMO_CLAIM_FRAGMENT);
    expect(body).not.toContain(DECISION_REASONING_FRAGMENT);
    expect(body).not.toContain("confidence:");
    expect(body).not.toContain("Miriam Okafor");
    expect(body).not.toContain("Senior Aviation Counsel");
    // Claims cited only by excluded sections stay out too.
    expect(body).not.toContain("transitions to ACTIVE");
  });

  it("includes cited claims and their sources with custody chains", () => {
    expect(packet.citedClaims.map((c) => c.claim.id)).toEqual(["clm-a4"]);
    expect(packet.citedSources.map((s) => s.id)).toEqual(["src-fdr"]);
    const body = renderPacketBody(packet);
    expect(body).toContain("chain of custody");
    expect(body).toContain("FDR recovered from wreckage");
  });
});

describe("buildEvidencePacket — production after preserving a conflict", () => {
  // Resolve CF-1 as preserve_both, then rebuild: SOE-3 becomes includable
  // with a mandatory conflict disclosure.
  const input = sampleInput("production");
  const conflict = input.conflictsById.get("cf-1")!;
  const claimA = input.claimsById.get(conflict.claimAId)!;
  const claimB = input.claimsById.get(conflict.claimBId)!;
  let n = 0;
  const result = applyDecision(
    {
      conflict,
      claimA,
      claimB,
      conflictsById: input.conflictsById,
      now: "2026-07-09T20:00:00Z",
      makeId: () => `d-${++n}`,
    },
    {
      conflictId: conflict.id,
      decisionType: "preserve_both",
      selectedClaimId: null,
      reasoning: "Both accounts preserved pending expert reconciliation of the timeline.",
      reviewerName: "Miriam Okafor",
      reviewerRole: "Senior Aviation Counsel",
    },
  );
  const claimsById = new Map(input.claimsById);
  for (const c of result.claims) claimsById.set(c.id, c);
  const conflictsById = new Map(input.conflictsById);
  conflictsById.set(result.conflict.id, result.conflict);
  const packet = buildEvidencePacket({ ...input, claimsById, conflictsById });

  it("includes the disclosure section with both claims", () => {
    const soe = packet.entries.find((e) => e.section.paragraphRef === "SOE-3");
    expect(soe?.disposition).toBe("included");
    expect(soe?.disclosures).toHaveLength(1);
    expect(packet.disclosedConflicts).toHaveLength(1);
    const body = renderPacketBody(packet);
    expect(body).toContain("Conflict disclosure");
    // Both sides appear, including the counterpart only cited via disclosure.
    expect(body).toContain("warning horn a good while before");
    expect(body).toContain("neither has been suppressed");
    // The preserve decision's internal reasoning still never leaks.
    expect(body).not.toContain("expert reconciliation");
  });
});

describe("buildEvidencePacket — production redacts privileged disclosure counterparts", () => {
  it("withholds a privileged claim that enters via conflict disclosure", () => {
    const source = sample.sources.find((s) => s.id === "src-memo")!;
    const privileged: Claim = {
      ...sample.claims.find((c) => c.id === "clm-a5")!,
      status: "preserved",
      relatedConflictIds: ["cf-x"],
    };
    const open: Claim = {
      ...sample.claims.find((c) => c.id === "clm-b5")!,
      status: "preserved",
      reportEligibility: "eligible",
      relatedConflictIds: ["cf-x"],
    };
    const conflict: Conflict = {
      id: "cf-x",
      caseId: "case-3407",
      claimAId: privileged.id,
      claimBId: open.id,
      conflictType: "interpretation",
      severity: "medium",
      status: "preserved_both",
      claimADisposition: "preserved",
      claimBDisposition: "preserved",
      summary: "SENTINEL privileged training conflict summary",
      createdAt: "2026-06-01T00:00:00Z",
      decisionIds: [],
    };
    const packet = buildEvidencePacket({
      caseFile: sample.caseFile,
      sections: [
        {
          id: "rpt-x",
          caseId: "case-3407",
          title: "Training",
          paragraphRef: "X-1",
          text: "Training compliance [FAA records].",
          claimIds: [open.id],
        },
      ],
      claimsById: new Map([
        [privileged.id, privileged],
        [open.id, open],
      ]),
      conflictsById: new Map([[conflict.id, conflict]]),
      sourcesById: new Map(sample.sources.map((s) => [s.id, s])),
      decisions: [],
      meta: meta("production"),
    });

    const entry = packet.entries[0];
    expect(entry.disposition).toBe("included");
    expect(entry.disclosures[0].claimA.redacted).toBe(true);
    expect(entry.disclosures[0].claimA.opaqueRef).toMatch(/^WITHHELD-\d{3}$/);
    expect(entry.disclosures[0].conflict.summary).toBe(
      "Counterpart withheld under privilege",
    );
    // The privileged source lands in the privilege log, not the appendix.
    expect(packet.privilegeLog.map((p) => p.source.id)).toEqual([source.id]);
    expect(packet.privilegeLog[0].sectionRefs[0]).toMatch(/^WITHHELD-\d{3}$/);
    expect(packet.citedSources.map((s) => s.id)).not.toContain(source.id);
    expect(packet.citedClaims.map((pc) => pc.claim.id)).not.toContain(privileged.id);
    const body = renderPacketBody(packet);
    expect(body).toContain("Withheld — privileged material");
    expect(body).toContain("Counterpart withheld under privilege");
    expect(body).not.toContain("SENTINEL privileged training conflict summary");
    expect(body).not.toContain(privileged.id);
    expect(body).not.toContain(MEMO_CLAIM_FRAGMENT);
    expect(body).not.toContain(MEMO_QUOTE_FRAGMENT);
  });
});

describe("buildEvidencePacket — production artifact metadata minimization", () => {
  it("replaces every non-included field with packet-local opaque metadata", () => {
    const input = sampleInput("production");
    const baseClaim = input.claimsById.get("clm-a5")!;
    const privileged: Claim = {
      ...baseClaim,
      id: "SENTINEL-PRIVILEGED-CLAIM-ID",
      text: "SENTINEL privileged claim text",
      pageRef: "SENTINEL privileged page reference",
      quote: "SENTINEL privileged quotation",
    };
    const section = {
      id: "SENTINEL-SECTION-ID",
      caseId: input.caseFile.id,
      title: "SENTINEL privileged section title",
      paragraphRef: "SENTINEL-PRIV-REF",
      text: "SENTINEL privileged section body",
      claimIds: [privileged.id],
      approvalState: "approved" as const,
      activeRevisionId: "SENTINEL-REVISION-ID",
      version: 99,
    };
    const production = buildEvidencePacket({
      ...input,
      sections: [section],
      claimsById: new Map([[privileged.id, privileged]]),
      conflictsById: new Map(),
      decisions: [],
    });

    const serialized = JSON.stringify(production);
    for (const sentinel of [
      section.id,
      section.title,
      section.paragraphRef,
      section.text,
      section.activeRevisionId,
      privileged.id,
      privileged.text,
      privileged.pageRef,
      privileged.quote,
    ]) {
      expect(serialized).not.toContain(sentinel);
      expect(renderPacketBody(production)).not.toContain(sentinel);
    }
    expect(production.entries[0].section.id).toMatch(/^WITHHELD-\d{3}$/);
    expect(production.privilegeLog[0].sectionRefs[0]).toBe(
      production.entries[0].section.id,
    );

    const internal = buildEvidencePacket({
      ...input,
      sections: [section],
      claimsById: new Map([[privileged.id, privileged]]),
      conflictsById: new Map(),
      decisions: [],
      meta: meta("internal"),
    });
    const internalBody = renderPacketBody(internal);
    expect(internalBody).toContain(section.title);
    expect(internalBody).toContain(section.paragraphRef);
    expect(internalBody).toContain(privileged.id);
    expect(internalBody).toContain("Miriam Okafor");
    expect(internalBody).toContain("Senior Aviation Counsel");
  });
});

describe("buildEvidencePacket — internal", () => {
  const packet = buildEvidencePacket(sampleInput("internal"));

  it("includes every section with status shown and full decision log", () => {
    expect(packet.stats).toEqual({
      included: sample.reportSections.length,
      excluded: 0,
      withheld: 0,
    });
    expect(packet.decisionLog).toHaveLength(1);
  });

  it("contains privileged material, marked, and reviewer reasoning", () => {
    const body = renderPacketBody(packet);
    expect(body).toContain("ATTORNEY WORK PRODUCT");
    expect(body).toContain("DO NOT PRODUCE");
    expect(body).toContain(MEMO_CLAIM_FRAGMENT);
    expect(body).toContain(DECISION_REASONING_FRAGMENT);
    expect(body).toContain("confidence:");
  });
});

describe("rendering safety and integrity", () => {
  it("escapes HTML in evidence content", () => {
    const input = sampleInput("internal");
    const claims = new Map(input.claimsById);
    const a4 = claims.get("clm-a4")!;
    claims.set("clm-a4", { ...a4, text: `<script>alert("xss")</script>` });
    const body = renderPacketBody(buildEvidencePacket({ ...input, claimsById: claims }));
    expect(body).not.toContain("<script>");
    expect(body).toContain("&lt;script&gt;");
  });

  it("escapeHtml covers the critical characters", () => {
    expect(escapeHtml(`<a href="x">&'</a>`)).toBe(
      "&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;",
    );
  });

  it("sha256Hex produces a stable 64-char hex digest", async () => {
    const hash = await sha256Hex("atlas argus");
    expect(hash).toMatch(/^[0-9a-f]{64}$/);
    expect(await sha256Hex("atlas argus")).toBe(hash);
    expect(await sha256Hex("atlas argus!")).not.toBe(hash);
  });

  it("embeds the hash and packet id in the standalone document", async () => {
    const packet = buildEvidencePacket(sampleInput("production"));
    const body = renderPacketBody(packet);
    const hash = await sha256Hex(body);
    const doc = renderPacketDocument(packet, body, hash);
    expect(doc).toContain("<!doctype html>");
    expect(doc).toContain(hash);
    expect(doc).toContain("pkt-test-1");
    expect(doc).toContain(body);
    expect(packetFilename(packet)).toBe(
      "atlas-argus-production-packet-case-3407-20260709T2100.html",
    );
  });
});
