import type { Claim, Conflict, ReportSection } from "./types";

export type SectionStatus =
  | "eligible"
  | "eligible_with_disclosure"
  | "blocked"
  | "privileged_material"
  | "needs_review";

export interface SectionImpact {
  status: SectionStatus;
  /** Claims that cause the blocking / warning state. */
  offendingClaimIds: string[];
  note: string;
}

/**
 * Derive a report section's usability from the claims it cites.
 * Priority: an unresolved conflict blocks outright; privileged material must
 * be withheld; anything unreviewed/excluded needs editorial attention;
 * preserved conflicts are usable only with the conflict disclosed.
 */
export function computeSectionImpact(
  section: Pick<ReportSection, "claimIds" | "paragraphRef">,
  claimsById: ReadonlyMap<string, Claim>,
  conflictsById: ReadonlyMap<string, Conflict>,
): SectionImpact {
  const cited = section.claimIds
    .map((id) => claimsById.get(id))
    .filter((c): c is Claim => c !== undefined);

  const blocked = cited.filter((c) => c.reportEligibility === "blocked_by_conflict");
  if (blocked.length > 0) {
    const conflictNames = new Set(
      blocked.flatMap((c) =>
        c.relatedConflictIds
          .map((id) => conflictsById.get(id))
          .filter((cf): cf is Conflict => cf !== undefined && cf.status === "unresolved")
          .map((cf) => cf.summary),
      ),
    );
    return {
      status: "blocked",
      offendingClaimIds: blocked.map((c) => c.id),
      note: `Report blocked: paragraph ${section.paragraphRef} references ${
        blocked.length === 1 ? "a disputed claim" : `${blocked.length} disputed claims`
      } (${[...conflictNames].join("; ")}).`,
    };
  }

  const privileged = cited.filter((c) => c.reportEligibility === "privileged");
  if (privileged.length > 0) {
    return {
      status: "privileged_material",
      offendingClaimIds: privileged.map((c) => c.id),
      note: `Paragraph ${section.paragraphRef} cites privileged material that must be withheld or replaced with a non-privileged source before production.`,
    };
  }

  const attention = cited.filter(
    (c) => c.reportEligibility === "needs_review" || c.reportEligibility === "excluded",
  );
  if (attention.length > 0) {
    const excluded = attention.filter((c) => c.reportEligibility === "excluded");
    return {
      status: "needs_review",
      offendingClaimIds: attention.map((c) => c.id),
      note:
        excluded.length > 0
          ? `Paragraph ${section.paragraphRef} cites ${excluded.length} excluded claim(s); the citation must be removed or replaced, and remaining support re-reviewed.`
          : `Paragraph ${section.paragraphRef} cites claims that still need review before the section can be used.`,
    };
  }

  const preserved = cited.filter((c) => c.status === "preserved");
  if (preserved.length > 0) {
    return {
      status: "eligible_with_disclosure",
      offendingClaimIds: preserved.map((c) => c.id),
      note: `Paragraph ${section.paragraphRef} is usable only if the preserved conflict is disclosed alongside both claims.`,
    };
  }

  return {
    status: "eligible",
    offendingClaimIds: [],
    note: `All cited claims are report-eligible with source citations intact.`,
  };
}

/** Report sections citing a given claim, with their current impact. */
export function sectionsCitingClaim(
  claimId: string,
  sections: readonly ReportSection[],
): ReportSection[] {
  return sections.filter((s) => s.claimIds.includes(claimId));
}
