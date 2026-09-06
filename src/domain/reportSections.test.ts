import { describe, expect, it } from "vitest";
import {
  createReportSection,
  updateReportSection,
  validateReportSection,
  type ReportSectionInput,
} from "./reportSections";
import * as sample from "../data/sampleCase";

const claimsById = new Map(sample.claims.map((c) => [c.id, c]));
const NOW = "2026-07-10T18:00:00Z";

function input(overrides: Partial<ReportSectionInput> = {}): ReportSectionInput {
  return {
    title: "Engine Performance",
    paragraphRef: "ENG-1",
    text: "Engine parameters remained within normal ranges throughout the approach [FDR Data Readout, p. 51].",
    claimIds: ["clm-a4"],
    ...overrides,
  };
}

const baseCtx = {
  now: NOW,
  eventId: "ev-1",
  actorName: "Miriam Okafor",
  actorRole: "Senior Aviation Counsel" as const,
  previousImpact: null,
  newImpact: "eligible" as const,
};

describe("validateReportSection", () => {
  it("rejects short titles, missing/duplicate refs, short text, missing citations", () => {
    expect(
      validateReportSection(input({ title: "ab" }), claimsById, sample.reportSections),
    ).toMatch(/title/i);
    expect(
      validateReportSection(input({ paragraphRef: " " }), claimsById, sample.reportSections),
    ).toMatch(/paragraph reference/i);
    expect(
      validateReportSection(
        input({ paragraphRef: "pc-1" }),
        claimsById,
        sample.reportSections,
      ),
    ).toMatch(/already used by “Probable Cause”/);
    expect(
      validateReportSection(input({ text: "too short" }), claimsById, sample.reportSections),
    ).toMatch(/Section text/);
    expect(
      validateReportSection(input({ claimIds: [] }), claimsById, sample.reportSections),
    ).toMatch(/cite at least one claim/i);
    expect(
      validateReportSection(
        input({ claimIds: ["clm-a4", "clm-a4"] }),
        claimsById,
        sample.reportSections,
      ),
    ).toMatch(/only once/i);
    expect(
      validateReportSection(
        input({ claimIds: ["clm-nope"] }),
        claimsById,
        sample.reportSections,
      ),
    ).toMatch(/not found: clm-nope/);
  });

  it("allows a section to keep its own paragraph reference while editing", () => {
    expect(
      validateReportSection(
        input({ paragraphRef: "PC-1" }),
        claimsById,
        sample.reportSections,
        "rpt-pc",
      ),
    ).toBeNull();
    expect(validateReportSection(input(), claimsById, sample.reportSections)).toBeNull();
  });
});

describe("createReportSection", () => {
  it("creates the section and audits the draft with its usability status", () => {
    const { section, auditEvent } = createReportSection(
      { ...baseCtx, sectionId: "rpt-7", caseId: "case-3407" },
      input(),
    );
    expect(section.id).toBe("rpt-7");
    expect(section.claimIds).toEqual(["clm-a4"]);
    expect(auditEvent.action).toBe("report section drafted");
    expect(auditEvent.subjectType).toBe("report_section");
    expect(auditEvent.previousStatus).toBeNull();
    expect(auditEvent.newStatus).toBe("eligible");
    expect(auditEvent.detail).toContain("clm-a4");
  });
});

describe("updateReportSection", () => {
  const existing = sample.reportSections.find((s) => s.id === "rpt-acft")!;

  it("audits citation changes and impact transitions", () => {
    const { section, auditEvent } = updateReportSection(
      {
        ...baseCtx,
        existing,
        previousImpact: "eligible",
        newImpact: "blocked",
      },
      {
        title: existing.title,
        paragraphRef: existing.paragraphRef,
        text: existing.text,
        claimIds: [...existing.claimIds, "clm-a1"],
      },
    );
    expect(section.id).toBe(existing.id);
    expect(section.claimIds).toContain("clm-a1");
    expect(auditEvent.action).toBe("report section revised");
    expect(auditEvent.detail).toContain("citations added: clm-a1");
    expect(auditEvent.previousStatus).toBe("eligible");
    expect(auditEvent.newStatus).toBe("blocked");
    expect(auditEvent.reportEligibilityChanged).toBe(true);
  });

  it("notes when nothing substantive changed", () => {
    const { auditEvent } = updateReportSection(
      { ...baseCtx, existing, previousImpact: "eligible", newImpact: "eligible" },
      {
        title: existing.title,
        paragraphRef: existing.paragraphRef,
        text: existing.text,
        claimIds: [...existing.claimIds],
      },
    );
    expect(auditEvent.detail).toContain("no substantive changes");
    expect(auditEvent.reportEligibilityChanged).toBe(false);
  });
});
