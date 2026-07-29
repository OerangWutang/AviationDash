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

function hasCommonShape(snap: Record<string, unknown>): boolean {
  return (
    Array.isArray(snap.claims) &&
    Array.isArray(snap.conflicts) &&
    Array.isArray(snap.conflictOrder) &&
    Array.isArray(snap.decisions) &&
    Array.isArray(snap.auditEvents) &&
    typeof snap.activeReviewerId === "string" &&
    snap.claims.length > 0 &&
    snap.conflictOrder.length > 0
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
  if (snap.reportSections !== null && !Array.isArray(snap.reportSections)) return null;
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

export function saveSnapshot(snapshot: CaseSnapshot): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(STORAGE_KEY, JSON.stringify(snapshot));
  } catch {
    // Quota or privacy-mode failure: the app keeps working, only persistence
    // degrades. Nothing to surface mid-workflow.
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
