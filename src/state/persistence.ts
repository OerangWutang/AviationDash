/**
 * Local working-copy persistence.
 *
 * The case's mutable record (claims, conflicts, decisions, audit trail) is
 * saved to this browser's localStorage so review work survives a reload.
 * This is honest client-side persistence — clearly labeled in the UI, not a
 * pretend backend. Static seed data (case file, sources, report sections,
 * reviewers) is never persisted; it always comes from the sample seed.
 *
 * The snapshot is versioned: anything unreadable or from another schema
 * version is discarded and the seed is used instead — a corrupt working copy
 * must never poison the record.
 */
import type {
  AuditEvent,
  Claim,
  Conflict,
  ReportSection,
  ReviewDecision,
} from "../domain/types";

export const STORAGE_KEY = "atlas-argus-case-v1";
export const SNAPSHOT_VERSION = 2 as const;

export interface CaseSnapshot {
  version: typeof SNAPSHOT_VERSION;
  claims: Claim[];
  conflicts: Conflict[];
  conflictOrder: string[];
  decisions: ReviewDecision[];
  auditEvents: AuditEvent[];
  /** null when migrated from a v1 snapshot that predates section drafting. */
  reportSections: ReportSection[] | null;
  activeReviewerId: string;
}

export interface SnapshotSource {
  claims: ReadonlyMap<string, Claim>;
  conflicts: ReadonlyMap<string, Conflict>;
  conflictOrder: readonly string[];
  decisions: ReadonlyMap<string, ReviewDecision>;
  auditEvents: readonly AuditEvent[];
  reportSections: readonly ReportSection[];
  activeReviewerId: string;
}

export function toSnapshot(state: SnapshotSource): CaseSnapshot {
  return {
    version: SNAPSHOT_VERSION,
    claims: [...state.claims.values()],
    conflicts: [...state.conflicts.values()],
    conflictOrder: [...state.conflictOrder],
    decisions: [...state.decisions.values()],
    auditEvents: [...state.auditEvents],
    reportSections: [...state.reportSections],
    activeReviewerId: state.activeReviewerId,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasStrings(value: Record<string, unknown>, fields: string[]): boolean {
  return fields.every((field) => typeof value[field] === "string");
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isNullableString(value: unknown): boolean {
  return value === null || typeof value === "string";
}

function isClaim(value: unknown): value is Claim {
  if (!isRecord(value)) return false;
  return (
    hasStrings(value, [
      "id",
      "caseId",
      "text",
      "sourceDocumentId",
      "pageRef",
      "quote",
      "evidenceQuality",
      "status",
      "privilegeStatus",
      "reportEligibility",
      "updatedAt",
    ]) &&
    typeof value.confidence === "number" &&
    Number.isFinite(value.confidence) &&
    isNullableString(value.reviewer) &&
    isStringArray(value.relatedConflictIds) &&
    (value.quoteVerification === undefined ||
      typeof value.quoteVerification === "string") &&
    (value.sourcePageExtractionId === undefined ||
      isNullableString(value.sourcePageExtractionId))
  );
}

function isConflict(value: unknown): value is Conflict {
  if (!isRecord(value)) return false;
  return (
    hasStrings(value, [
      "id",
      "caseId",
      "claimAId",
      "claimBId",
      "conflictType",
      "severity",
      "status",
      "summary",
      "createdAt",
      "claimADisposition",
      "claimBDisposition",
    ]) &&
    isStringArray(value.decisionIds) &&
    (value.version === undefined || Number.isInteger(value.version))
  );
}

function isDecision(value: unknown): value is ReviewDecision {
  if (!isRecord(value)) return false;
  return (
    hasStrings(value, [
      "id",
      "conflictId",
      "decisionType",
      "reasoning",
      "reviewerName",
      "reviewerRole",
      "createdAt",
      "previousStatus",
      "newStatus",
      "reportImpact",
    ]) && isNullableString(value.selectedClaimId)
  );
}

function isAuditEvent(value: unknown): value is AuditEvent {
  if (!isRecord(value)) return false;
  return (
    hasStrings(value, [
      "id",
      "caseId",
      "at",
      "actor",
      "actorRole",
      "action",
      "subjectType",
      "subjectId",
      "detail",
    ]) &&
    isNullableString(value.previousStatus) &&
    isNullableString(value.newStatus) &&
    typeof value.reportEligibilityChanged === "boolean"
  );
}

function isReportSection(value: unknown): value is ReportSection {
  if (!isRecord(value)) return false;
  return (
    hasStrings(value, ["id", "caseId", "title", "paragraphRef", "text"]) &&
    isStringArray(value.claimIds) &&
    (value.approvalState === undefined ||
      value.approvalState === "draft" ||
      value.approvalState === "approved") &&
    (value.activeRevisionId === undefined || isNullableString(value.activeRevisionId)) &&
    (value.version === undefined || Number.isInteger(value.version))
  );
}

function uniqueIds(rows: unknown[]): boolean {
  const ids = rows.map((row) => (isRecord(row) ? row.id : undefined));
  return ids.every((id) => typeof id === "string") && new Set(ids).size === ids.length;
}

function hasCommonShape(snap: Record<string, unknown>): boolean {
  if (
    !Array.isArray(snap.claims) ||
    !Array.isArray(snap.conflicts) ||
    !isStringArray(snap.conflictOrder) ||
    !Array.isArray(snap.decisions) ||
    !Array.isArray(snap.auditEvents) ||
    typeof snap.activeReviewerId !== "string" ||
    snap.claims.length === 0 ||
    snap.conflictOrder.length === 0
  ) {
    return false;
  }
  if (
    !snap.claims.every(isClaim) ||
    !snap.conflicts.every(isConflict) ||
    !snap.decisions.every(isDecision) ||
    !snap.auditEvents.every(isAuditEvent) ||
    !uniqueIds(snap.claims) ||
    !uniqueIds(snap.conflicts) ||
    !uniqueIds(snap.decisions) ||
    !uniqueIds(snap.auditEvents)
  ) {
    return false;
  }
  const conflictIds = new Set(snap.conflicts.map((conflict) => conflict.id));
  return (
    snap.conflictOrder.length === conflictIds.size &&
    new Set(snap.conflictOrder).size === snap.conflictOrder.length &&
    snap.conflictOrder.every((id) => conflictIds.has(id))
  );
}

/**
 * Parse and structurally validate a stored snapshot; null on any doubt.
 * v1 snapshots (pre section-drafting) are migrated losslessly with
 * `reportSections: null` — the caller falls back to the seed sections.
 */
export function parseSnapshot(json: string): CaseSnapshot | null {
  let raw: unknown;
  try {
    raw = JSON.parse(json);
  } catch {
    return null;
  }
  if (typeof raw !== "object" || raw === null) return null;
  const snap = raw as Record<string, unknown>;
  if (!hasCommonShape(snap)) return null;
  if (snap.version === 1) {
    return {
      ...(snap as object),
      version: SNAPSHOT_VERSION,
      reportSections: null,
    } as CaseSnapshot;
  }
  if (snap.version !== SNAPSHOT_VERSION) return null;
  if (
    snap.reportSections !== null &&
    (!Array.isArray(snap.reportSections) ||
      !snap.reportSections.every(isReportSection) ||
      !uniqueIds(snap.reportSections))
  ) {
    return null;
  }
  return snap as unknown as CaseSnapshot;
}

function storage(): Storage | null {
  try {
    // Some environments (jsdom) expose a localStorage global that is not a
    // functional Storage; treat that the same as no storage at all.
    if (
      typeof localStorage === "undefined" ||
      typeof localStorage.getItem !== "function" ||
      typeof localStorage.setItem !== "function"
    ) {
      return null;
    }
    return localStorage;
  } catch {
    return null;
  }
}

export function loadSnapshot(): CaseSnapshot | null {
  const store = storage();
  if (!store) return null;
  try {
    const json = store.getItem(STORAGE_KEY);
    return json ? parseSnapshot(json) : null;
  } catch {
    return null;
  }
}

export function saveSnapshot(snapshot: CaseSnapshot): string | null {
  const store = storage();
  if (!store) return "Browser storage is unavailable; this working copy is not saved.";
  try {
    store.setItem(STORAGE_KEY, JSON.stringify(snapshot));
    return null;
  } catch {
    return "Browser storage is full or unavailable; recent work is not saved.";
  }
}

export function clearSnapshot(): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(STORAGE_KEY);
  } catch {
    // Same as above.
  }
}
