/**
 * Evidence packet export.
 *
 * Two packet types with different disclosure doctrine:
 *
 * - "internal": attorney work product for the case team. Contains everything —
 *   all report sections whatever their status, privileged material (marked
 *   DO NOT PRODUCE), internal assessments, and the full decision log with
 *   reasoning.
 *
 * - "production": an external-facing packet. Only report sections that are
 *   `eligible` or `eligible_with_disclosure` are included; blocked and
 *   unreviewed sections are excluded and identified in the manifest (no
 *   silent gaps). Privileged material is withheld in full and listed in a
 *   privilege log by identity only — never by content. Internal review
 *   assessments (confidence, evidence quality, reviewer identity, decision
 *   reasoning) are work product and never appear.
 *
 * `buildEvidencePacket` and the renderers are pure; the caller supplies ids,
 * timestamps, and persistence. The rendered body is hashed (SHA-256) and the
 * hash is embedded in the final document footer and recorded in the audit
 * trail, tying the artifact to the record.
 */
import type {
  CaseFile,
  Claim,
  Conflict,
  ReviewDecision,
  ReviewerRole,
  ReportSection,
  SourceDocument,
} from "./types";
import { WITHHOLDING_PRIVILEGE } from "./types";
import { computeSectionImpact, type SectionImpact } from "./report";
import type { DecisionPermission } from "./permissions";

export type PacketType = "internal" | "production";

export interface PacketMeta {
  packetId: string;
  type: PacketType;
  generatedAt: string;
  generatedByName: string;
  generatedByRole: ReviewerRole;
}

export type PacketDisposition = "included" | "excluded" | "withheld";

export interface PacketSectionEntry {
  section: ReportSection;
  impact: SectionImpact;
  disposition: PacketDisposition;
  reason: string;
  disclosures: PacketDisclosure[];
}

/** A claim as it appears in the packet. Privileged counterparts use an opaque ref. */
export interface PacketClaim {
  claim: Claim;
  redacted: boolean;
  opaqueRef: string | null;
}

export interface PacketDisclosure {
  conflict: Conflict;
  claimA: PacketClaim;
  claimB: PacketClaim;
}

export interface PrivilegeLogEntry {
  source: SourceDocument;
  sectionRefs: string[];
}

export interface EvidencePacket {
  caseFile: CaseFile;
  meta: PacketMeta;
  entries: PacketSectionEntry[];
  citedClaims: PacketClaim[];
  citedSources: SourceDocument[];
  disclosedConflicts: PacketDisclosure[];
  privilegeLog: PrivilegeLogEntry[];
  decisionLog: { decision: ReviewDecision; conflictSummary: string }[] | null;
  stats: { included: number; excluded: number; withheld: number };
}

export interface PacketBuildInput {
  caseFile: CaseFile;
  sections: readonly ReportSection[];
  claimsById: ReadonlyMap<string, Claim>;
  conflictsById: ReadonlyMap<string, Conflict>;
  sourcesById: ReadonlyMap<string, SourceDocument>;
  decisions: readonly ReviewDecision[];
  meta: PacketMeta;
}

/**
 * Packet generation is a legal/evidence-handling act reserved for counsel.
 */
export function packetPermission(
  type: PacketType,
  role: ReviewerRole,
): DecisionPermission {
  if ((type === "internal" || type === "production") && role !== "Senior Aviation Counsel") {
    return {
      allowed: false,
      reason: "Evidence packet generation is reserved for Senior Aviation Counsel.",
    };
  }
  return { allowed: true, reason: null };
}

function isWithholding(claim: Claim): boolean {
  return WITHHOLDING_PRIVILEGE.includes(claim.privilegeStatus);
}

function internalReason(impact: SectionImpact): string {
  switch (impact.status) {
    case "eligible":
      return "Included — report-eligible.";
    case "eligible_with_disclosure":
      return "Included — usable with conflict disclosure.";
    case "blocked":
      return "Included for internal review — BLOCKED for report use (unresolved conflict).";
    case "privileged_material":
      return "Included for internal review — privileged; do not produce.";
    case "needs_review":
      return "Included for internal review — cited claims still need review.";
  }
}

export function buildEvidencePacket(input: PacketBuildInput): EvidencePacket {
  const { caseFile, sections, claimsById, conflictsById, sourcesById, decisions, meta } =
    input;
  const production = meta.type === "production";

  let withheldCounter = 0;
  let excludedCounter = 0;
  const nextOpaqueRef = (disposition: "withheld" | "excluded") => {
    if (disposition === "withheld") {
      withheldCounter += 1;
      return `WITHHELD-${String(withheldCounter).padStart(3, "0")}`;
    }
    excludedCounter += 1;
    return `EXCLUDED-${String(excludedCounter).padStart(3, "0")}`;
  };

  const redactedClaimRefs = new Map<string, string>();
  const packetClaim = (claim: Claim): PacketClaim => {
    const redacted = production && isWithholding(claim);
    let opaqueRef: string | null = null;
    if (redacted) {
      opaqueRef = redactedClaimRefs.get(claim.id) ?? nextOpaqueRef("withheld");
      redactedClaimRefs.set(claim.id, opaqueRef);
    }
    return { claim, redacted, opaqueRef };
  };

  // Production privilege-log entries expose document identity and packet-local
  // references, never database claim/section identifiers.
  const loggedPrivilege = new Map<string, PrivilegeLogEntry>();
  const logPrivilege = (claim: Claim, opaqueRef: string) => {
    const source = sourcesById.get(claim.sourceDocumentId);
    if (!source) return;
    const existing = loggedPrivilege.get(source.id);
    if (existing) {
      if (!existing.sectionRefs.includes(opaqueRef)) existing.sectionRefs.push(opaqueRef);
      return;
    }
    loggedPrivilege.set(source.id, { source, sectionRefs: [opaqueRef] });
  };

  const entries: PacketSectionEntry[] = sections.map((section) => {
    const impact = computeSectionImpact(section, claimsById, conflictsById);
    const containsPrivileged = section.claimIds.some((claimId) => {
      const claim = claimsById.get(claimId);
      return claim !== undefined && isWithholding(claim);
    });

    let disposition: PacketDisposition;
    let reason: string;
    if (!production) {
      disposition = "included";
      reason = internalReason(impact);
    } else if (containsPrivileged) {
      disposition = "withheld";
      reason = "Withheld under privilege; see privilege log.";
    } else if (impact.status === "eligible") {
      disposition = "included";
      reason = "Included — all cited claims report-eligible.";
    } else if (impact.status === "eligible_with_disclosure") {
      disposition = "included";
      reason = "Included with conflict disclosure.";
    } else if (impact.status === "privileged_material") {
      disposition = "withheld";
      reason = "Withheld under privilege; see privilege log.";
    } else {
      disposition = "excluded";
      reason = "Excluded from production.";
    }

    // Preserved conflicts among this section's cited claims must be disclosed
    // alongside the text — never resolved silently.
    const disclosures: PacketDisclosure[] = [];
    if (disposition === "included") {
      const seen = new Set<string>();
      for (const claimId of section.claimIds) {
        const claim = claimsById.get(claimId);
        if (!claim || claim.status !== "preserved") continue;
        for (const conflictId of claim.relatedConflictIds) {
          const conflict = conflictsById.get(conflictId);
          if (!conflict || conflict.status !== "preserved_both" || seen.has(conflict.id))
            continue;
          seen.add(conflict.id);
          const a = claimsById.get(conflict.claimAId);
          const b = claimsById.get(conflict.claimBId);
          if (a && b) {
            const claimA = packetClaim(a);
            const claimB = packetClaim(b);
            const hasRedactedCounterpart = claimA.redacted || claimB.redacted;
            const disclosureConflict =
              production && hasRedactedCounterpart
                ? {
                    ...conflict,
                    claimAId: claimA.redacted ? (claimA.opaqueRef ?? "WITHHELD") : claimA.claim.id,
                    claimBId: claimB.redacted ? (claimB.opaqueRef ?? "WITHHELD") : claimB.claim.id,
                    summary: "Counterpart withheld under privilege",
                    decisionIds: [],
                  }
                : conflict;
            disclosures.push({
              conflict: disclosureConflict,
              claimA,
              claimB,
            });
            if (production) {
              if (claimA.redacted && claimA.opaqueRef) logPrivilege(a, claimA.opaqueRef);
              if (claimB.redacted && claimB.opaqueRef) logPrivilege(b, claimB.opaqueRef);
            }
          }
        }
      }
    }

    if (production && disposition !== "included") {
      const opaqueRef = nextOpaqueRef(disposition);
      if (disposition === "withheld") {
        for (const claimId of section.claimIds) {
          const claim = claimsById.get(claimId);
          if (claim && isWithholding(claim)) logPrivilege(claim, opaqueRef);
        }
      }
      const sanitizedSection: ReportSection = {
        id: opaqueRef,
        caseId: section.caseId,
        title: disposition === "withheld" ? "Withheld material" : "Excluded material",
        paragraphRef: opaqueRef,
        text: "",
        claimIds: [],
      };
      const sanitizedImpact: SectionImpact = {
        status: disposition === "withheld" ? "privileged_material" : "needs_review",
        offendingClaimIds: [],
        note: reason,
      };
      return {
        section: sanitizedSection,
        impact: sanitizedImpact,
        disposition,
        reason,
        disclosures: [],
      };
    }

    return { section, impact, disposition, reason, disclosures };
  });

  // Claims cited by included sections, plus disclosure counterparts.
  const citedById = new Map<string, PacketClaim>();
  for (const entry of entries) {
    if (entry.disposition !== "included") continue;
    for (const claimId of entry.section.claimIds) {
      const claim = claimsById.get(claimId);
      if (claim) citedById.set(claim.id, packetClaim(claim));
    }
    for (const disclosure of entry.disclosures) {
      for (const pc of [disclosure.claimA, disclosure.claimB]) {
        if (!pc.redacted) citedById.set(pc.claim.id, pc);
      }
    }
  }
  const citedClaims = [...citedById.values()].sort((x, y) =>
    x.claim.id.localeCompare(y.claim.id),
  );

  // Source appendix: sources of non-redacted cited claims. A redacted claim's
  // source is privileged — it belongs in the privilege log, not the appendix.
  const sourceIds = new Set(
    citedClaims.filter((pc) => !pc.redacted).map((pc) => pc.claim.sourceDocumentId),
  );
  const citedSources = [...sourceIds]
    .map((id) => sourcesById.get(id))
    .filter((s): s is SourceDocument => s !== undefined)
    .sort((x, y) => x.id.localeCompare(y.id));

  const disclosedConflicts = [
    ...new Map(
      entries
        .flatMap((e) => e.disclosures)
        .map((d) => [d.conflict.id, d] as const),
    ).values(),
  ];

  const privilegeLog = production ? [...loggedPrivilege.values()] : [];

  const decisionLog = production
    ? null
    : [...decisions]
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
        .map((decision) => ({
          decision,
          conflictSummary:
            conflictsById.get(decision.conflictId)?.summary ?? decision.conflictId,
        }));

  const stats = {
    included: entries.filter((e) => e.disposition === "included").length,
    excluded: entries.filter((e) => e.disposition === "excluded").length,
    withheld: entries.filter((e) => e.disposition === "withheld").length,
  };

  return {
    caseFile,
    meta,
    entries,
    citedClaims,
    citedSources,
    disclosedConflicts,
    privilegeLog,
    decisionLog,
    stats,
  };
}

/* ── Rendering ─────────────────────────────────────────────────────────── */

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const e = escapeHtml;

function fmtUtc(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(
    d.getUTCHours(),
  )}:${pad(d.getUTCMinutes())}Z`;
}

const REDACTED_TEXT = "[Withheld — privileged material; see privilege log]";

function renderClaimCitation(pc: PacketClaim, sourcesById: Map<string, SourceDocument>) {
  if (pc.redacted) {
    const reference = pc.opaqueRef ? ` (${e(pc.opaqueRef)})` : "";
    return `<p class="redacted">${REDACTED_TEXT}${reference}</p>`;
  }
  const source = sourcesById.get(pc.claim.sourceDocumentId);
  const sourceLine = source
    ? `${e(source.title)}${source.docketRef ? `, ${e(source.docketRef)}` : ""}, ${e(pc.claim.pageRef)}`
    : e(pc.claim.pageRef);
  return `<p class="claim-text">${e(pc.claim.text)}</p>
<p class="cite">${sourceLine}</p>
<blockquote>&ldquo;${e(pc.claim.quote)}&rdquo;</blockquote>`;
}

function renderDisclosure(d: PacketDisclosure, sourcesById: Map<string, SourceDocument>) {
  const summary =
    d.claimA.redacted || d.claimB.redacted
      ? "Counterpart withheld under privilege"
      : d.conflict.summary;
  return `<div class="disclosure">
<p class="disclosure-head">Conflict disclosure — ${e(summary)}</p>
<div class="disclosure-claim"><span class="side">Claim A</span>${renderClaimCitation(d.claimA, sourcesById)}</div>
<div class="disclosure-claim"><span class="side">Claim B</span>${renderClaimCitation(d.claimB, sourcesById)}</div>
<p class="disclosure-note">Both claims are preserved in the case record; neither has been suppressed.</p>
</div>`;
}

/**
 * Render the packet body (everything except the outer document shell and the
 * integrity footer). Deterministic for a given packet — this exact string is
 * what gets hashed.
 */
export function renderPacketBody(p: EvidencePacket): string {
  const production = p.meta.type === "production";
  const sourcesById = new Map(p.citedSources.map((s) => [s.id, s]));
  // Disclosure/appendix rendering may reference privileged sources on the
  // internal packet; include all sources reachable through cited claims there.
  const parts: string[] = [];

  const stamp = production
    ? `<div class="stamp production">PRODUCTION COPY</div>`
    : `<div class="stamp internal">ATTORNEY WORK PRODUCT — PRIVILEGED &amp; CONFIDENTIAL — INTERNAL USE ONLY</div>`;

  const scope = production
    ? "This packet contains report sections and supporting evidence cleared for external production. Material under privilege designation is withheld in full and listed in the privilege log. Sections citing claims in unresolved conflicts are excluded and identified in the manifest. Internal review assessments (confidence, evidence-quality ratings, reviewer identities, and decision reasoning) are attorney work product and are not included."
    : "Prepared for case-team use. This packet reflects the complete current state of evidence review, including privileged material (marked), blocked sections, and the full decision log with reviewer reasoning. Do not produce or distribute outside the case team.";

  parts.push(`<header>
${stamp}
<h1>Evidence Packet</h1>
<table class="caption">
<tr><th>Case</th><td>${e(p.caseFile.name)}</td></tr>
<tr><th>Docket</th><td>${e(p.caseFile.docketRef)}</td></tr>
<tr><th>Aircraft</th><td>${e(p.caseFile.aircraft)}</td></tr>
<tr><th>Accident date</th><td>${e(p.caseFile.accidentDate)}</td></tr>
<tr><th>Location</th><td>${e(p.caseFile.location)}</td></tr>
<tr><th>Matter type</th><td>${e(p.caseFile.matterType)}</td></tr>
</table>
<p class="genline">${
  production
    ? `Generated ${fmtUtc(p.meta.generatedAt)} &middot; Packet ${e(p.meta.packetId)} &middot; Atlas Argus`
    : `Generated ${fmtUtc(p.meta.generatedAt)} by ${e(p.meta.generatedByName)}, ${e(p.meta.generatedByRole)} &middot; Packet ${e(p.meta.packetId)} &middot; Atlas Argus`
}</p>
<p class="scope">${scope}</p>
</header>`);

  parts.push(`<section>
<h2>Manifest</h2>
<table class="manifest">
<tr><th>&para;</th><th>Section</th><th>Status</th><th>Disposition</th></tr>
${p.entries
  .map(
    (entry) =>
      `<tr><td>${e(entry.section.paragraphRef)}</td><td>${e(entry.section.title)}</td><td>${e(
        entry.impact.status.replace(/_/g, " "),
      )}</td><td>${e(entry.reason)}</td></tr>`,
  )
  .join("\n")}
</table>
<p class="stats">${p.stats.included} section(s) included &middot; ${p.stats.excluded} excluded &middot; ${p.stats.withheld} withheld under privilege.</p>
</section>`);

  const rendered = p.entries.filter((entry) => entry.disposition === "included");
  parts.push(`<section>
<h2>Report sections</h2>
${rendered
  .map((entry) => {
    const marks: string[] = [];
    if (!production && entry.impact.status === "privileged_material") {
      marks.push(`<p class="mark privileged">PRIVILEGED — DO NOT PRODUCE</p>`);
    }
    if (!production && entry.impact.status === "blocked") {
      marks.push(`<p class="mark blocked">BLOCKED FOR REPORT USE — ${e(entry.impact.note)}</p>`);
    }
    return `<article class="report-section">
<h3>${e(entry.section.title)} <span class="pref">&para; ${e(entry.section.paragraphRef)}</span></h3>
${marks.join("\n")}
<p class="body-text">${e(entry.section.text)}</p>
${entry.disclosures.map((d) => renderDisclosure(d, sourcesById)).join("\n")}
</article>`;
  })
  .join("\n")}
${rendered.length === 0 ? `<p class="empty">No report sections are currently eligible for this packet type. The manifest above identifies what is excluded and why.</p>` : ""}
</section>`);

  parts.push(`<section>
<h2>Appendix A — Cited claims</h2>
${p.citedClaims
  .map((pc) => {
    if (pc.redacted) {
      return `<article class="appendix-claim"><h4>Withheld claim</h4><p class="redacted">${REDACTED_TEXT}</p></article>`;
    }
    const internalMeta = production
      ? ""
      : `<p class="cite">Status: ${e(pc.claim.status)} &middot; report eligibility: ${e(
          pc.claim.reportEligibility.replace(/_/g, " "),
        )} &middot; confidence: ${Math.round(pc.claim.confidence * 100)}% &middot; evidence quality: ${e(
          pc.claim.evidenceQuality,
        )} &middot; reviewer: ${e(pc.claim.reviewer ?? "—")}</p>`;
    const privilegedMark =
      !production && WITHHOLDING_PRIVILEGE.includes(pc.claim.privilegeStatus)
        ? `<p class="mark privileged">PRIVILEGED (${e(pc.claim.privilegeStatus.replace(/_/g, " "))}) — DO NOT PRODUCE</p>`
        : "";
    return `<article class="appendix-claim">
<h4>${e(pc.claim.id)}</h4>
${privilegedMark}
${renderClaimCitation(pc, sourcesById)}
${internalMeta}
</article>`;
  })
  .join("\n")}
</section>`);

  parts.push(`<section>
<h2>Appendix B — Source documents &amp; chain of custody</h2>
${p.citedSources
  .map(
    (source) => `<article class="appendix-source">
<h4>${e(source.title)}</h4>
<p class="cite">${e(source.type.replace(/_/g, " "))} &middot; ${e(source.origin)} &middot; custodian: ${e(
      source.custodian,
    )}${source.docketRef ? ` &middot; ${e(source.docketRef)}` : ""}</p>
<p class="cite">SHA-256: <code>${e(source.sha256)}</code> &middot; ingested ${fmtUtc(source.ingestedAt)}</p>
<ol class="custody">
${source.custody
  .map((ev) => `<li><span class="when">${fmtUtc(ev.at)}</span> ${e(ev.actor)} — ${e(ev.action)}</li>`)
  .join("\n")}
</ol>
</article>`,
  )
  .join("\n")}
</section>`);

  if (production && p.privilegeLog.length > 0) {
    parts.push(`<section>
<h2>Privilege log</h2>
<p class="scope">The following material is withheld in full under privilege designation. Entries identify the withheld document; contents are not described.</p>
<table class="manifest">
<tr><th>Document</th><th>Type</th><th>Designation</th><th>Custodian</th><th>Referenced by</th></tr>
${p.privilegeLog
  .map(
    (entry) =>
      `<tr><td>${e(entry.source.title)}</td><td>${e(entry.source.type.replace(/_/g, " "))}</td><td>${e(
        entry.source.privilegeStatus.replace(/_/g, " "),
      )}</td><td>${e(entry.source.custodian)}</td><td>&para; ${entry.sectionRefs.map(e).join(", ")}</td></tr>`,
  )
  .join("\n")}
</table>
</section>`);
  }

  if (p.decisionLog) {
    parts.push(`<section>
<h2>Decision log</h2>
${
  p.decisionLog.length === 0
    ? `<p class="empty">No review decisions recorded yet.</p>`
    : p.decisionLog
        .map(
          ({ decision, conflictSummary }) => `<article class="decision">
<h4>${e(decision.decisionType.replace(/_/g, " "))} &middot; <span class="when">${fmtUtc(decision.createdAt)}</span></h4>
<p class="cite">${e(conflictSummary)}</p>
<p class="cite">${e(decision.reviewerName)}, ${e(decision.reviewerRole)} &middot; ${e(
            decision.previousStatus.replace(/_/g, " "),
          )} &rarr; ${e(decision.newStatus.replace(/_/g, " "))}</p>
<p class="body-text">${e(decision.reasoning)}</p>
<p class="cite">Report impact: ${e(decision.reportImpact)}</p>
</article>`,
        )
        .join("\n")
}
</section>`);
  }

  return parts.join("\n");
}

const PACKET_CSS = `
body { margin: 0; background: #f0f0eb; }
.packet { max-width: 800px; margin: 0 auto; padding: 48px 56px; background: #fff; color: #16181d; font: 14px/1.6 Georgia, "Times New Roman", serif; }
h1 { font-size: 26px; letter-spacing: 0.04em; margin: 18px 0 14px; }
h2 { font-size: 17px; border-bottom: 2px solid #16181d; padding-bottom: 4px; margin: 32px 0 14px; }
h3 { font-size: 15px; margin: 20px 0 8px; }
h4 { font-size: 13.5px; margin: 16px 0 6px; }
.pref, .when { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px; color: #5c626e; font-weight: normal; }
.stamp { font: bold 12px/1.4 Arial, sans-serif; letter-spacing: 0.08em; text-align: center; padding: 8px 12px; border: 2px solid; }
.stamp.internal { color: #8a1f1a; border-color: #8a1f1a; }
.stamp.production { color: #1d4f8a; border-color: #1d4f8a; }
.caption { border-collapse: collapse; width: 100%; margin: 10px 0; }
.caption th { text-align: left; font: bold 11px/1.4 Arial, sans-serif; letter-spacing: 0.05em; text-transform: uppercase; color: #5c626e; padding: 3px 14px 3px 0; white-space: nowrap; vertical-align: top; }
.caption td { padding: 3px 0; }
.genline { font: 11.5px/1.5 Arial, sans-serif; color: #5c626e; }
.scope { font-size: 12.5px; color: #3c414b; border-left: 3px solid #c9cdd6; padding-left: 12px; }
.manifest { border-collapse: collapse; width: 100%; font-size: 12px; }
.manifest th, .manifest td { border: 1px solid #c9cdd6; padding: 5px 8px; text-align: left; vertical-align: top; }
.manifest th { font-family: Arial, sans-serif; font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase; background: #f4f5f7; }
.stats { font: 11.5px Arial, sans-serif; color: #5c626e; }
.body-text { margin: 8px 0; }
.claim-text { font-weight: bold; margin: 6px 0 2px; }
.cite { font: 11.5px/1.5 Arial, sans-serif; color: #5c626e; margin: 2px 0; }
.cite code { font-size: 10.5px; }
blockquote { margin: 6px 0 10px; padding: 6px 12px; border-left: 3px solid #c9cdd6; font-style: italic; color: #3c414b; font-size: 13px; }
.mark { font: bold 11px/1.4 Arial, sans-serif; letter-spacing: 0.06em; padding: 5px 10px; }
.mark.privileged { color: #6a3fae; border: 1px solid #6a3fae; }
.mark.blocked { color: #8a1f1a; border: 1px solid #8a1f1a; }
.redacted { font: italic 13px Georgia, serif; color: #6a3fae; }
.disclosure { border: 1px solid #c9cdd6; background: #fafafa; padding: 10px 14px; margin: 12px 0; }
.disclosure-head { font: bold 12px Arial, sans-serif; letter-spacing: 0.03em; margin: 0 0 6px; }
.disclosure-claim { margin: 8px 0; }
.disclosure-claim .side { font: bold 10.5px Arial, sans-serif; letter-spacing: 0.08em; color: #5c626e; }
.disclosure-note { font: italic 12px Georgia, serif; color: #3c414b; margin: 6px 0 0; }
.custody { font-size: 12px; padding-left: 18px; margin: 4px 0; }
.custody li { margin: 2px 0; }
.appendix-claim, .appendix-source, .decision { margin-bottom: 18px; }
.empty { font-style: italic; color: #5c626e; }
footer { margin-top: 36px; border-top: 1px solid #c9cdd6; padding-top: 10px; }
.integrity { font: 10.5px ui-monospace, Menlo, Consolas, monospace; color: #5c626e; word-break: break-all; }
@page { margin: 18mm; }
@media print { body { background: #fff; } .packet { padding: 0; max-width: none; } }
`;

/**
 * Wrap the body in a standalone, self-contained HTML document. The integrity
 * line states exactly what the hash covers: the packet body markup, not the
 * document shell or the hash line itself.
 */
export function renderPacketDocument(
  p: EvidencePacket,
  body: string,
  bodySha256: string,
): string {
  const title = `Evidence Packet — ${p.caseFile.name} (${p.meta.type})`;
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${e(title)}</title>
<style>${PACKET_CSS}</style>
</head>
<body>
<div class="packet">
${body}
<footer>
<p class="integrity">Integrity: SHA-256 of the packet body (this document's content between header and footer, excluding this line) = ${e(bodySha256)}</p>
<p class="integrity">Packet ${e(p.meta.packetId)} &middot; generated ${fmtUtc(p.meta.generatedAt)} &middot; recorded in the Atlas Argus audit trail.</p>
</footer>
</div>
</body>
</html>`;
}

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function packetFilename(p: EvidencePacket): string {
  const ts = p.meta.generatedAt.replace(/[-:]/g, "").slice(0, 13); // YYYYMMDDTHHMM
  return `atlas-argus-${p.meta.type}-packet-${p.caseFile.id}-${ts}.html`;
}
