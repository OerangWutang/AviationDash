import { focusedClaim, useStore } from "../state/store";
import { sectionsCitingClaim, computeSectionImpact } from "../domain/report";
import { formatDateTime } from "../lib/format";
import { ClaimStatusBadge, EligibilityBadge, SectionStatusBadge, SeverityDot } from "./StatusBadge";
import { PrivilegeBadge } from "./PrivilegeBadge";
import { SourceDocumentCard } from "./SourceDocumentCard";
import { AuditTrailItem } from "./AuditTrailItem";
import { QuoteVerificationBadge } from "./QuoteVerificationBadge";

export function EvidenceRail() {
  const { state, selectConflict } = useStore();
  const claim = focusedClaim(state);

  if (!claim) {
    return (
      <aside className="rail" aria-label="Evidence Rail">
        <h2 className="rail-heading">Evidence Rail</h2>
        <p className="muted empty-note">Select a claim to inspect its evidence.</p>
      </aside>
    );
  }

  const source = state.sources.get(claim.sourceDocumentId);
  const reportSafe = claim.reportEligibility === "eligible";

  // Conflicts involving this claim directly, plus conflicts whose claims
  // share the same source document (source-level exposure).
  const relatedConflicts = [...state.conflicts.values()].filter((cf) => {
    if (cf.claimAId === claim.id || cf.claimBId === claim.id) return true;
    const a = state.claims.get(cf.claimAId);
    const b = state.claims.get(cf.claimBId);
    return (
      a?.sourceDocumentId === claim.sourceDocumentId ||
      b?.sourceDocumentId === claim.sourceDocumentId
    );
  });

  const claimAudit = state.auditEvents
    .filter(
      (e) =>
        (e.subjectType === "claim" && e.subjectId === claim.id) ||
        (e.subjectType === "source" && e.subjectId === claim.sourceDocumentId) ||
        (e.subjectType === "conflict" && claim.relatedConflictIds.includes(e.subjectId)),
    )
    .slice(0, 6);

  const citedIn = sectionsCitingClaim(claim.id, state.reportSections);

  return (
    <aside className="rail" aria-label="Evidence Rail">
      <h2 className="rail-heading">Evidence Rail</h2>

      <section className="rail-section">
        <h3 className="rail-title">Selected claim</h3>
        <p className="rail-claim-text">{claim.text}</p>
        <div className="badge-row">
          <ClaimStatusBadge status={claim.status} />
          <PrivilegeBadge status={claim.privilegeStatus} />
          <EligibilityBadge eligibility={claim.reportEligibility} />
        </div>
        <p className={`report-safe ${reportSafe ? "safe" : "unsafe"}`}>
          {reportSafe
            ? "REPORT-SAFE — this claim may be cited in report text."
            : `NOT REPORT-SAFE — ${claim.reportEligibility.replace(/_/g, " ")}.`}
        </p>
      </section>

      {source && (
        <section className="rail-section">
          <h3 className="rail-title">Source document</h3>
          <SourceDocumentCard source={source} />
        </section>
      )}

      <section className="rail-section">
        <h3 className="rail-title">Citation</h3>
        <p className="mono muted rail-pageref">{claim.pageRef}</p>
        <blockquote className="quote-block">“{claim.quote}”</blockquote>
        <QuoteVerificationBadge verification={claim.quoteVerification} />
      </section>

      {source && (
        <section className="rail-section">
          <h3 className="rail-title">Chain of custody</h3>
          <ol className="custody-list">
            {source.custody.map((event, i) => (
              <li key={i} className="custody-item">
                <span className="mono custody-time">{formatDateTime(event.at)}</span>
                <span className="custody-body">
                  <strong>{event.actor}</strong> — {event.action}
                </span>
              </li>
            ))}
          </ol>
        </section>
      )}

      <section className="rail-section">
        <h3 className="rail-title">Related conflicts</h3>
        {relatedConflicts.length === 0 ? (
          <p className="muted empty-note">No related conflicts.</p>
        ) : (
          <ul className="rail-conflict-list">
            {relatedConflicts.map((cf) => (
              <li key={cf.id}>
                <button
                  type="button"
                  className="rail-conflict-link"
                  onClick={() => selectConflict(cf.id)}
                >
                  <SeverityDot severity={cf.severity} />
                  <span className="mono">{cf.id.toUpperCase()}</span>
                  <span className="rail-conflict-summary">{cf.summary}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rail-section">
        <h3 className="rail-title">Report impact</h3>
        {citedIn.length === 0 ? (
          <p className="muted empty-note">Not cited in any report paragraph.</p>
        ) : (
          <ul className="impact-list">
            {citedIn.map((section) => {
              const impact = computeSectionImpact(section, state.claims, state.conflicts);
              return (
                <li key={section.id} className="impact-item">
                  <div className="impact-head">
                    <span>
                      {section.title}{" "}
                      <span className="mono muted">¶ {section.paragraphRef}</span>
                    </span>
                    <SectionStatusBadge status={impact.status} />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="rail-section">
        <h3 className="rail-title">Audit events</h3>
        <ul className="audit-list">
          {claimAudit.map((event) => (
            <AuditTrailItem key={event.id} event={event} compact />
          ))}
        </ul>
      </section>
    </aside>
  );
}
