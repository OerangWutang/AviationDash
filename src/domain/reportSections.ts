/**
 * Report section drafting and revision.
 *
 * Doctrine:
 * - No report text without source references: a section cannot be saved
 *   without citing at least one claim, and every cited claim must exist.
 * - Paragraph references are unique — a citation like "¶ PC-1" must resolve
 *   to exactly one paragraph.
 * - Sections are never deleted; drafting and every revision are audited with
 *   the section's usability status before and after.
 */
import type { AuditEvent, Claim, ReportSection, ReviewerRole } from "./types";
import type { SectionStatus } from "./report";

export const MIN_SECTION_TITLE_LENGTH = 3;
export const MIN_SECTION_TEXT_LENGTH = 40;

export interface ReportSectionInput {
  title: string;
  paragraphRef: string;
  text: string;
  claimIds: string[];
}

export function validateReportSection(
  input: ReportSectionInput,
  claimsById: ReadonlyMap<string, Claim>,
  sections: readonly ReportSection[],
  editingSectionId: string | null = null,
): string | null {
  if (input.title.trim().length < MIN_SECTION_TITLE_LENGTH) {
    return `A section title of at least ${MIN_SECTION_TITLE_LENGTH} characters is required.`;
  }
  const ref = input.paragraphRef.trim();
  if (ref.length === 0) {
    return "A paragraph reference (e.g. PC-2) is required.";
  }
  const duplicate = sections.find(
    (section) =>
      section.id !== editingSectionId &&
      section.paragraphRef.toLowerCase() === ref.toLowerCase(),
  );
  if (duplicate) {
    return `Paragraph reference “${ref}” is already used by “${duplicate.title}”.`;
  }
  if (input.text.trim().length < MIN_SECTION_TEXT_LENGTH) {
    return `Section text of at least ${MIN_SECTION_TEXT_LENGTH} characters is required.`;
  }
  if (input.claimIds.length === 0) {
    return "No report text without source references — cite at least one claim.";
  }
  if (new Set(input.claimIds).size !== input.claimIds.length) {
    return "Each claim may be cited only once per report section.";
  }
  for (const claimId of input.claimIds) {
    if (!claimsById.has(claimId)) {
      return `Cited claim not found: ${claimId}.`;
    }
  }
  return null;
}

export interface SaveSectionContext {
  now: string;
  eventId: string;
  actorName: string;
  actorRole: ReviewerRole;
  /** Usability status before the change (null when drafting). */
  previousImpact: SectionStatus | null;
  /** Usability status the saved section will have. */
  newImpact: SectionStatus;
}

function sectionOf(
  base: Pick<ReportSection, "id" | "caseId">,
  input: ReportSectionInput,
): ReportSection {
  return {
    id: base.id,
    caseId: base.caseId,
    title: input.title.trim(),
    paragraphRef: input.paragraphRef.trim(),
    text: input.text.trim(),
    claimIds: [...input.claimIds],
  };
}

export function createReportSection(
  ctx: SaveSectionContext & { sectionId: string; caseId: string },
  input: ReportSectionInput,
): { section: ReportSection; auditEvent: AuditEvent } {
  const section = sectionOf({ id: ctx.sectionId, caseId: ctx.caseId }, input);
  const auditEvent: AuditEvent = {
    id: ctx.eventId,
    caseId: ctx.caseId,
    at: ctx.now,
    actor: ctx.actorName,
    actorRole: ctx.actorRole,
    action: "report section drafted",
    subjectType: "report_section",
    subjectId: section.id,
    previousStatus: null,
    newStatus: ctx.newImpact,
    detail: `¶ ${section.paragraphRef} “${section.title}” drafted, citing ${
      section.claimIds.length
    } claim(s): ${section.claimIds.join(", ")}.`,
    reportEligibilityChanged: false,
  };
  return { section, auditEvent };
}

export function updateReportSection(
  ctx: SaveSectionContext & { existing: ReportSection },
  input: ReportSectionInput,
): { section: ReportSection; auditEvent: AuditEvent } {
  const { existing } = ctx;
  const section = sectionOf(existing, input);

  const added = section.claimIds.filter((id) => !existing.claimIds.includes(id));
  const removed = existing.claimIds.filter((id) => !section.claimIds.includes(id));
  const changes: string[] = [];
  if (added.length > 0) changes.push(`citations added: ${added.join(", ")}`);
  if (removed.length > 0) changes.push(`citations removed: ${removed.join(", ")}`);
  if (section.text !== existing.text) changes.push("text revised");
  if (section.title !== existing.title) changes.push("title changed");
  if (section.paragraphRef !== existing.paragraphRef) {
    changes.push(`paragraph reference ${existing.paragraphRef} → ${section.paragraphRef}`);
  }

  const auditEvent: AuditEvent = {
    id: ctx.eventId,
    caseId: existing.caseId,
    at: ctx.now,
    actor: ctx.actorName,
    actorRole: ctx.actorRole,
    action: "report section revised",
    subjectType: "report_section",
    subjectId: section.id,
    previousStatus: ctx.previousImpact,
    newStatus: ctx.newImpact,
    detail: `¶ ${section.paragraphRef} “${section.title}”: ${
      changes.length > 0 ? changes.join("; ") : "no substantive changes"
    }.`,
    reportEligibilityChanged: ctx.previousImpact !== ctx.newImpact,
  };
  return { section, auditEvent };
}
