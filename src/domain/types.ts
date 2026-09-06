/**
 * Atlas Argus domain model.
 *
 * Core doctrine: every important fact must trace claim → source → conflict →
 * review decision → report impact, and nothing is ever silently deleted.
 * Rejected / superseded / preserved claims stay visible; history is append-only.
 */

export type ClaimStatus =
  | "unreviewed"
  | "supported"
  | "disputed"
  | "superseded"
  | "rejected"
  | "preserved"
  | "escalated";

export type ConflictStatus =
  | "unresolved"
  | "accepted_claim_a"
  | "accepted_claim_b"
  | "preserved_both"
  | "escalated"
  | "source_unreliable"
  | "closed";

export type ReportEligibility =
  | "eligible"
  | "blocked_by_conflict"
  | "privileged"
  | "needs_review"
  | "excluded";

export type PrivilegeStatus =
  | "public"
  | "confidential"
  | "attorney_client"
  | "work_product"
  | "restricted";

/** Privilege statuses that withhold material from report use entirely. */
export const WITHHOLDING_PRIVILEGE: readonly PrivilegeStatus[] = [
  "attorney_client",
  "work_product",
  "restricted",
];

export type EvidenceQuality =
  | "primary"
  | "corroborated"
  | "secondary"
  | "uncorroborated";

export type SourceType =
  | "flight_recorder"
  | "official_report"
  | "deposition"
  | "expert_report"
  | "operational_record"
  | "atc_transcript"
  | "weather_data"
  | "internal_memo";

export type ConflictType =
  | "timeline"
  | "factual"
  | "interpretation"
  | "source_reliability";

export type ConflictSeverity = "critical" | "high" | "medium" | "low";

export type DecisionType =
  | "accept_claim_a"
  | "accept_claim_b"
  | "preserve_both"
  | "mark_unresolved"
  | "escalate"
  | "mark_source_unreliable";

export type ReviewerRole =
  | "Senior Aviation Counsel"
  | "Accident Reconstruction Expert"
  | "Claims Reviewer"
  | "Safety Investigator";

export interface Reviewer {
  id: string;
  name: string;
  role: ReviewerRole;
}

export interface CustodyEvent {
  at: string; // ISO-8601 UTC
  actor: string;
  action: string;
}

export interface SourceDocument {
  id: string;
  caseId: string;
  title: string;
  type: SourceType;
  /** Where the document originally came from (agency, party, custodian firm). */
  origin: string;
  custodian: string;
  docketRef: string | null;
  ingestedAt: string;
  sha256: string;
  privilegeStatus: PrivilegeStatus;
  custody: CustodyEvent[];
  /** True once a document has been uploaded and extracted, so its pages can be
   *  cited and its quotes checked. False for matter records that predate
   *  ingestion. */
  isIngested?: boolean;
  /** SHA-256 of the stored bytes. Unlike `sha256`, which is a legacy display
   *  field that nothing can verify for seeded rows, this is recomputable. */
  contentSha256?: string;
  pageCount?: number;
  originalFilename?: string;
  sizeBytes?: number;
}

/** How a page's text was obtained. `failed` is a reported outcome, not an
 *  absence: the page is still citable, but nothing can check a quote against
 *  it automatically. */
export type ExtractionMethod = "native" | "ocr" | "failed";

export type ExtractionFailureCode =
  | "native_extract_error"
  | "invalid_media_box"
  | "pixel_limit"
  | "raster_timeout"
  | "ocr_timeout"
  | "ocr_unavailable"
  | "page_parse_error"
  | "total_text_budget_exhausted";

export interface SourcePage {
  id: string;
  pageNumber: number;
  extractionMethod: ExtractionMethod;
  text: string;
  /** True when the page hit the byte cap. A quote *absent* from truncated text
   *  is unknown, not disproved — the UI must not imply otherwise. */
  textTruncated: boolean;
  /** Percent, or null for native text (which is the document's own text layer,
   *  not an interpretation of pixels). */
  ocrConfidence: number | null;
  failureCode: ExtractionFailureCode | null;
  failureDetail: string | null;
  /** Suggested `pageRef` pre-fill. The reviewer owns the real citation. */
  defaultPageRef: string;
}

/** What is actually known about a claim's quote. */
export type QuoteVerification =
  | "verified_exact"
  | "manual_verification_required"
  | "manually_verified"
  | "legacy_unverified";

export interface Claim {
  id: string;
  caseId: string;
  text: string;
  sourceDocumentId: string;
  pageRef: string;
  quote: string;
  /** Extraction/assessment confidence, 0–1. */
  confidence: number;
  evidenceQuality: EvidenceQuality;
  status: ClaimStatus;
  privilegeStatus: PrivilegeStatus;
  reportEligibility: ReportEligibility;
  /** Last reviewer to act on this claim, if any. */
  reviewer: string | null;
  updatedAt: string;
  relatedConflictIds: string[];
  /** Server-computed. Optional because local demo mode has no ingestion. */
  quoteVerification?: QuoteVerification;
  /** The immutable extracted page this quote was checked against. */
  sourcePageExtractionId?: string | null;
}

export interface Conflict {
  id: string;
  caseId: string;
  claimAId: string;
  claimBId: string;
  conflictType: ConflictType;
  severity: ConflictSeverity;
  status: ConflictStatus;
  summary: string;
  createdAt: string;
  decisionIds: string[];
  /**
   * What this conflict says about each of its two claims. A claim's global
   * `status` is the aggregate of these across every conflict it is in — kept on
   * the conflict so deciding one never erases another's outcome.
   */
  claimADisposition: ClaimStatus;
  claimBDisposition: ClaimStatus;
  /** Server-side optimistic-concurrency token. Undefined in local/demo mode. */
  version?: number;
}

export interface ReviewDecision {
  id: string;
  conflictId: string;
  decisionType: DecisionType;
  /** For accept_* this is the accepted claim; for mark_source_unreliable, the claim rejected in this conflict. */
  selectedClaimId: string | null;
  reasoning: string;
  reviewerName: string;
  reviewerRole: ReviewerRole;
  createdAt: string;
  previousStatus: ConflictStatus;
  newStatus: ConflictStatus;
  /** Human-readable summary of how report eligibility changed. */
  reportImpact: string;
}

export type AuditSubjectType =
  | "claim"
  | "conflict"
  | "source"
  | "report_section"
  | "export"
  | "reviewer";

export interface AuditEvent {
  id: string;
  caseId: string;
  at: string;
  actor: string;
  actorRole: string;
  action: string;
  subjectType: AuditSubjectType;
  subjectId: string;
  previousStatus: string | null;
  newStatus: string | null;
  detail: string;
  reportEligibilityChanged: boolean;
}

export interface ReportSection {
  id: string;
  caseId: string;
  title: string;
  paragraphRef: string;
  /** Draft text; every sentence carries inline bracketed source citations. */
  text: string;
  claimIds: string[];
  /**
   * Server mode only: production packets require an approved revision
   * (Senior Aviation Counsel). Every save reverts a section to "draft" until
   * re-approved. Undefined in local/demo mode, which has no revision concept
   * and treats every eligible section as includable.
   */
  approvalState?: "draft" | "approved";
  activeRevisionId?: string | null;
  /** Server-side optimistic-concurrency token. Undefined in local/demo mode. */
  version?: number;
}

export interface CaseFile {
  id: string;
  name: string;
  aircraft: string;
  accidentDate: string;
  location: string;
  matterType: string;
  /** Empty only for the authenticated no-matter workspace. */
  status: "open" | "";
  docketRef: string;
}
