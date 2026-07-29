/**
 * Claim extraction and conflict flagging.
 *
 * Doctrine:
 * - A claim is born from a source document and inherits its privilege
 *   designation — material extracted from an attorney-client memo is
 *   privileged from the moment it exists, whatever the workflow does later.
 * - Every claim must carry a page reference and a supporting quote; a claim
 *   with no source location cannot be created.
 * - Flagging a conflict disputes both claims and blocks their report use
 *   until the conflict is reviewed. The same pair cannot carry two open
 *   conflicts.
 * - Both operations are audited with previous → new status.
 */
import type {
  AuditEvent,
  Claim,
  Conflict,
  ConflictSeverity,
  ConflictType,
  EvidenceQuality,
  ReviewerRole,
  SourceDocument,
} from "./types";
import { aggregateForClaim } from "./decisions";
import { computeClaimEligibility } from "./eligibility";

export const MIN_CLAIM_TEXT_LENGTH = 15;
export const MIN_QUOTE_LENGTH = 10;
export const MIN_SUMMARY_LENGTH = 15;

/* ── Claim extraction ──────────────────────────────────────────────────── */

export interface NewClaimInput {
  sourceDocumentId: string;
  text: string;
  pageRef: string;
  quote: string;
  /** 0–1. */
  confidence: number;
  evidenceQuality: EvidenceQuality;
  /** The extracted page to check this quote against, in server mode. Local
   *  demo mode has no ingestion, so it is absent there. */
  sourcePageExtractionId?: string | null;
}

export function validateNewClaim(input: NewClaimInput): string | null {
  if (input.text.trim().length < MIN_CLAIM_TEXT_LENGTH) {
    return `Claim text of at least ${MIN_CLAIM_TEXT_LENGTH} characters is required.`;
  }
  if (input.pageRef.trim().length === 0) {
    return "A page/section reference is required — every claim must trace to a source location.";
  }
  if (input.quote.trim().length < MIN_QUOTE_LENGTH) {
    return `A supporting quote of at least ${MIN_QUOTE_LENGTH} characters is required.`;
  }
  if (input.confidence < 0 || input.confidence > 1 || Number.isNaN(input.confidence)) {
    return "Confidence must be between 0 and 100%.";
  }
  return null;
}

export interface CreateClaimContext {
  source: SourceDocument;
  claimId: string;
  eventId: string;
  now: string;
  actorName: string;
  actorRole: ReviewerRole;
}

export function createClaim(
  ctx: CreateClaimContext,
  input: NewClaimInput,
): { claim: Claim; auditEvent: AuditEvent } {
  const validationError = validateNewClaim(input);
  if (validationError) {
    throw new Error(validationError);
  }
  const claim: Claim = {
    id: ctx.claimId,
    caseId: ctx.source.caseId,
    text: input.text.trim(),
    sourceDocumentId: ctx.source.id,
    pageRef: input.pageRef.trim(),
    quote: input.quote.trim(),
    confidence: input.confidence,
    evidenceQuality: input.evidenceQuality,
    status: "unreviewed",
    // Privilege is inherited from the source, never chosen at extraction.
    privilegeStatus: ctx.source.privilegeStatus,
    reportEligibility: "needs_review",
    reviewer: null,
    updatedAt: ctx.now,
    relatedConflictIds: [],
  };
  claim.reportEligibility = computeClaimEligibility(claim, new Map());

  const auditEvent: AuditEvent = {
    id: ctx.eventId,
    caseId: claim.caseId,
    at: ctx.now,
    actor: ctx.actorName,
    actorRole: ctx.actorRole,
    action: "claim extracted",
    subjectType: "claim",
    subjectId: claim.id,
    previousStatus: null,
    newStatus: claim.status,
    detail: `Extracted from ${ctx.source.title}, ${claim.pageRef}. Privilege inherited from source: ${ctx.source.privilegeStatus}.`,
    reportEligibilityChanged: false,
  };
  return { claim, auditEvent };
}

/* ── Conflict flagging ─────────────────────────────────────────────────── */

export interface FlagConflictInput {
  claimAId: string;
  claimBId: string;
  conflictType: ConflictType;
  severity: ConflictSeverity;
  summary: string;
}

const OPEN_CONFLICT_STATUSES = ["unresolved", "escalated"] as const;

export function validateFlagConflict(
  input: FlagConflictInput,
  claimsById: ReadonlyMap<string, Claim>,
  conflictsById: ReadonlyMap<string, Conflict>,
): string | null {
  if (input.claimAId === input.claimBId) {
    return "Select two different claims.";
  }
  const claimA = claimsById.get(input.claimAId);
  const claimB = claimsById.get(input.claimBId);
  if (!claimA || !claimB) {
    return "Both claims must exist.";
  }
  if (input.summary.trim().length < MIN_SUMMARY_LENGTH) {
    return `A conflict summary of at least ${MIN_SUMMARY_LENGTH} characters is required.`;
  }
  for (const conflict of conflictsById.values()) {
    const samePair =
      (conflict.claimAId === input.claimAId && conflict.claimBId === input.claimBId) ||
      (conflict.claimAId === input.claimBId && conflict.claimBId === input.claimAId);
    if (
      samePair &&
      (OPEN_CONFLICT_STATUSES as readonly string[]).includes(conflict.status)
    ) {
      return `These claims already have an open conflict (${conflict.id.toUpperCase()}).`;
    }
  }
  return null;
}

export interface FlagConflictContext {
  claimsById: ReadonlyMap<string, Claim>;
  conflictsById: ReadonlyMap<string, Conflict>;
  conflictId: string;
  now: string;
  makeEventId: () => string;
  actorName: string;
  actorRole: ReviewerRole;
}

export interface FlagConflictResult {
  conflict: Conflict;
  claims: Claim[];
  auditEvents: AuditEvent[];
}

export function flagConflict(
  ctx: FlagConflictContext,
  input: FlagConflictInput,
): FlagConflictResult {
  const validationError = validateFlagConflict(input, ctx.claimsById, ctx.conflictsById);
  if (validationError) {
    throw new Error(validationError);
  }
  const claimA = ctx.claimsById.get(input.claimAId)!;
  const claimB = ctx.claimsById.get(input.claimBId)!;

  const conflict: Conflict = {
    id: ctx.conflictId,
    caseId: claimA.caseId,
    claimAId: claimA.id,
    claimBId: claimB.id,
    conflictType: input.conflictType,
    severity: input.severity,
    status: "unresolved",
    summary: input.summary.trim(),
    createdAt: ctx.now,
    decisionIds: [],
    // A freshly flagged conflict disputes both its claims.
    claimADisposition: "disputed",
    claimBDisposition: "disputed",
  };

  const conflictsById = new Map(ctx.conflictsById);
  conflictsById.set(conflict.id, conflict);

  const disputeClaim = (claim: Claim): Claim => {
    // The claim's global status is the aggregate over ALL its conflicts (now
    // including this one), not simply "disputed" — a claim already resolved in
    // another conflict keeps its more-decisive standing.
    const relatedConflictIds = [...claim.relatedConflictIds, conflict.id];
    const next: Claim = {
      ...claim,
      status: aggregateForClaim({ id: claim.id, relatedConflictIds }, conflictsById),
      reviewer: ctx.actorName,
      updatedAt: ctx.now,
      relatedConflictIds,
    };
    next.reportEligibility = computeClaimEligibility(next, conflictsById);
    return next;
  };

  const nextA = disputeClaim(claimA);
  const nextB = disputeClaim(claimB);

  const auditEvents: AuditEvent[] = [
    {
      id: ctx.makeEventId(),
      caseId: conflict.caseId,
      at: ctx.now,
      actor: ctx.actorName,
      actorRole: ctx.actorRole,
      action: "conflict flagged",
      subjectType: "conflict",
      subjectId: conflict.id,
      previousStatus: null,
      newStatus: "unresolved",
      detail: `${conflict.summary} (severity: ${conflict.severity}, type: ${conflict.conflictType}).`,
      reportEligibilityChanged: true,
    },
    ...[
      { prev: claimA, next: nextA },
      { prev: claimB, next: nextB },
    ].map(({ prev, next }) => ({
      id: ctx.makeEventId(),
      caseId: conflict.caseId,
      at: ctx.now,
      actor: ctx.actorName,
      actorRole: ctx.actorRole,
      action: "claim status updated",
      subjectType: "claim" as const,
      subjectId: next.id,
      previousStatus: prev.status,
      newStatus: next.status,
      detail: `Flagged in ${conflict.id.toUpperCase()}. Report eligibility: ${prev.reportEligibility} → ${next.reportEligibility}`,
      reportEligibilityChanged: prev.reportEligibility !== next.reportEligibility,
    })),
  ];

  return { conflict, claims: [nextA, nextB], auditEvents };
}

/** Fold away differences that are typography rather than substance.
 *
 *  Mirrors `normalize_quote_text` in `server/src/atlas_argus/ingestion.py`.
 *  The two MUST agree: this one decides what the editor tells a reviewer about
 *  their quote, and the Python one decides whether the server accepts it. If
 *  they drift, the UI promises matches the server rejects.
 *
 *  De-hyphenation is a real heuristic and can be wrong — a genuinely
 *  hyphenated compound broken across lines ("cross-\ncheck") becomes
 *  "crosscheck". Line-wrap hyphens are far more common in extracted PDF text,
 *  so it is applied deliberately, as a known imprecision rather than a rule.
 *
 *  KNOWN DIVERGENCE: Python uses `str.casefold()`, which maps "ß" to "ss";
 *  JavaScript has no casefold and `toLowerCase()` leaves it alone. A quote
 *  containing such a character can therefore be judged differently here than
 *  on the server. The server is always authoritative — this function only
 *  drives an advisory hint in the editor — so the failure mode is a hint that
 *  disagrees with the result, never an accepted unverified quote.
 */
export function normalizeQuoteText(value: string): string {
  const QUOTE_MAP: Record<string, string> = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "′": "'", "″": '"',
    "–": "-", "—": "-", "―": "-", "−": "-",
    " ": " ",
  };
  return value
    .normalize("NFKC")
    .replace(/­/g, "")
    .replace(/(\w)-\s*\n\s*(\w)/g, "$1$2")
    .replace(/[‘’‚‛“”„′″–—―− ]/g,
      (character) => QUOTE_MAP[character] ?? character)
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

/** Whether a quote appears on a page, by the same rule the server applies. */
export function quoteAppearsOnPage(quote: string, pageText: string): boolean {
  const normalized = normalizeQuoteText(quote);
  if (!normalized) return false;
  return normalizeQuoteText(pageText).includes(normalized);
}
