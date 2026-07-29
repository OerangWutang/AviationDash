import type {
  ClaimStatus,
  ConflictSeverity,
  ConflictStatus,
  EvidenceQuality,
  PrivilegeStatus,
  ReportEligibility,
  SourceType,
} from "../domain/types";
import type { SectionStatus } from "../domain/report";

/** "2026-06-28T14:32:00Z" → "2026-06-28 14:32Z" (always UTC). */
export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(
    d.getUTCHours(),
  )}:${pad(d.getUTCMinutes())}Z`;
}

export function formatDate(iso: string): string {
  return formatDateTime(iso).slice(0, 10);
}

export function formatConfidence(c: number): string {
  return `${Math.round(c * 100)}%`;
}

const enumLabel = (value: string) => value.replace(/_/g, " ");

export const claimStatusLabel = (s: ClaimStatus) => enumLabel(s);
export const conflictStatusLabel = (s: ConflictStatus) => enumLabel(s);
export const eligibilityLabel = (s: ReportEligibility) => enumLabel(s);
export const privilegeLabel = (s: PrivilegeStatus) => enumLabel(s);
export const evidenceQualityLabel = (s: EvidenceQuality) => enumLabel(s);
export const sourceTypeLabel = (s: SourceType) => enumLabel(s);
export const sectionStatusLabel = (s: SectionStatus) => enumLabel(s);

/** Visual tone buckets shared by all badges. */
export type Tone = "neutral" | "positive" | "caution" | "negative" | "privileged" | "info";

export const claimStatusTone: Record<ClaimStatus, Tone> = {
  unreviewed: "neutral",
  supported: "positive",
  disputed: "negative",
  superseded: "neutral",
  rejected: "negative",
  preserved: "info",
  escalated: "caution",
};

export const conflictStatusTone: Record<ConflictStatus, Tone> = {
  unresolved: "negative",
  accepted_claim_a: "positive",
  accepted_claim_b: "positive",
  preserved_both: "info",
  escalated: "caution",
  source_unreliable: "caution",
  closed: "neutral",
};

export const eligibilityTone: Record<ReportEligibility, Tone> = {
  eligible: "positive",
  blocked_by_conflict: "negative",
  privileged: "privileged",
  needs_review: "caution",
  excluded: "neutral",
};

export const privilegeTone: Record<PrivilegeStatus, Tone> = {
  public: "neutral",
  confidential: "info",
  attorney_client: "privileged",
  work_product: "privileged",
  restricted: "privileged",
};

export const severityTone: Record<ConflictSeverity, Tone> = {
  critical: "negative",
  high: "caution",
  medium: "info",
  low: "neutral",
};

export const sectionStatusTone: Record<SectionStatus, Tone> = {
  eligible: "positive",
  eligible_with_disclosure: "info",
  blocked: "negative",
  privileged_material: "privileged",
  needs_review: "caution",
};

export function truncateHash(sha256: string): string {
  return `${sha256.slice(0, 12)}…${sha256.slice(-6)}`;
}
