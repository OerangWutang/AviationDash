import { useState } from "react";
import { effectiveMatterRole, useStore } from "../../state/store";
import { computeSectionImpact } from "../../domain/report";
import { packetPermission } from "../../domain/packet";
import { Badge, EligibilityBadge, SectionStatusBadge } from "../StatusBadge";
import { SectionEditor } from "../SectionEditor";

export function ReportsView() {
  const { state, selectConflict, selectClaim, selectView, approveReportSection } = useStore();
  /** null = closed; "new" = drafting; otherwise the section id being revised. */
  const [editorFor, setEditorFor] = useState<string | null>(null);
  const [busySectionId, setBusySectionId] = useState<string | null>(null);
  const [approveError, setApproveError] = useState<string | null>(null);

  const reviewer = state.reviewers.find((r) => r.id === state.activeReviewerId);
  const matterRole = effectiveMatterRole(state);
  const approvalPermission =
    reviewer && matterRole
      ? packetPermission("production", matterRole)
      : { allowed: false, reason: "No active matter role is available." };

  const handleApprove = async (sectionId: string) => {
    setApproveError(null);
    setBusySectionId(sectionId);
    const outcome = await approveReportSection(sectionId);
    setBusySectionId(null);
    if (!outcome.ok) {
      setApproveError(outcome.error);
    }
  };

  return (
    <div className="view-stack">
      <header className="view-header">
        <h2>Report sections</h2>
        <p className="muted">
          Draft report paragraphs with live usability status. A paragraph is blocked
          while any cited claim sits in an unresolved conflict; privileged material must
          be replaced before production. No report text exists without source citations.
        </p>
        <div className="save-row header-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setEditorFor("new")}
          >
            Draft new section
          </button>
          <button type="button" className="btn-link" onClick={() => selectView("export")}>
            Assemble evidence packet →
          </button>
        </div>
      </header>

      {approveError && <p className="form-error">{approveError}</p>}

      {editorFor !== null && (
        <SectionEditor
          key={editorFor}
          existing={
            editorFor === "new"
              ? null
              : (state.reportSections.find((s) => s.id === editorFor) ?? null)
          }
          onClose={() => setEditorFor(null)}
        />
      )}
      {state.reportSections.map((section) => {
        const impact = computeSectionImpact(section, state.claims, state.conflicts);
        return (
          <article key={section.id} className="panel report-entry">
            <div className="impact-head">
              <h3 className="report-title">
                {section.title} <span className="mono muted">¶ {section.paragraphRef}</span>
              </h3>
              <span className="badge-row">
                <SectionStatusBadge status={impact.status} />
                <button
                  type="button"
                  className="btn-link edit-section"
                  onClick={() => setEditorFor(section.id)}
                >
                  Revise
                </button>
              </span>
            </div>
            <p className="report-text">{section.text}</p>
            <p className={`impact-note${impact.status === "blocked" ? " blocked" : ""}`}>
              {impact.note}
            </p>
            {state.dataMode === "server" && section.approvalState && (
              <p className="badge-row">
                <Badge tone={section.approvalState === "approved" ? "positive" : "caution"}>
                  {section.approvalState === "approved"
                    ? "Approved for production"
                    : "Not approved for production"}
                </Badge>
                {section.approvalState === "draft" && (
                  <button
                    type="button"
                    className="btn-link"
                    disabled={!approvalPermission.allowed || busySectionId === section.id}
                    title={approvalPermission.allowed ? undefined : (approvalPermission.reason ?? undefined)}
                    onClick={() => void handleApprove(section.id)}
                  >
                    {busySectionId === section.id
                      ? "Approving…"
                      : "Approve for production"}
                  </button>
                )}
              </p>
            )}
            <div className="report-claims">
              <span className="muted">Cited claims:</span>
              {section.claimIds.map((claimId) => {
                const claim = state.claims.get(claimId);
                if (!claim) return null;
                return (
                  <button
                    key={claimId}
                    type="button"
                    className="claim-chip"
                    title={claim.text}
                    onClick={() => {
                      const conflictId = claim.relatedConflictIds.find((id) =>
                        state.conflicts.has(id),
                      );
                      if (conflictId) {
                        selectConflict(conflictId);
                        selectClaim(claim.id);
                      } else {
                        selectClaim(claim.id);
                        selectView("claims");
                      }
                    }}
                  >
                    <span className="mono">{claimId.replace("clm-", "").toUpperCase()}</span>
                    <EligibilityBadge eligibility={claim.reportEligibility} />
                  </button>
                );
              })}
            </div>
          </article>
        );
      })}
    </div>
  );
}
