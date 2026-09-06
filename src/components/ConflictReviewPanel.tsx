import { useState } from "react";
import { effectiveMatterRole, useStore } from "../state/store";
import { conflictStatusLabel, formatDateTime } from "../lib/format";
import { ClaimComparisonCard } from "./ClaimComparisonCard";
import { ConflictStatusBadge, SeverityBadge } from "./StatusBadge";
import { ReviewDecisionForm } from "./ReviewDecisionForm";
import { DecisionHistory } from "./DecisionHistory";
import { ReportImpactPreview } from "./ReportImpactPreview";
import { OriginalEvidenceDialog } from "./OriginalEvidenceDialog";

export function ConflictReviewPanel() {
  const { state, selectClaim } = useStore();
  const [openEvidenceFor, setOpenEvidenceFor] = useState<"a" | "b" | null>(null);
  const conflict = state.conflicts.get(state.selectedConflictId);
  if (!conflict) {
    return <p className="muted empty-note">Select a conflict from the review queue.</p>;
  }
  const claimA = state.claims.get(conflict.claimAId);
  const claimB = state.claims.get(conflict.claimBId);
  if (!claimA || !claimB) return null;
  const sourceA = state.sources.get(claimA.sourceDocumentId);
  const sourceB = state.sources.get(claimB.sourceDocumentId);
  if (!sourceA || !sourceB) return null;

  const focusedId = state.selectedClaimId ?? claimA.id;
  const conflictDecisions = conflict.decisionIds
    .map((id) => state.decisions.get(id))
    .filter((d) => d !== undefined)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  const matterRole = effectiveMatterRole(state);
  const canOpenSourceA =
    state.dataMode === "server" && sourceA.isIngested && Boolean(sourceA.contentSha256);
  const canOpenSourceB =
    state.dataMode === "server" && sourceB.isIngested && Boolean(sourceB.contentSha256);

  return (
    <div className="conflict-panel">
      <header className="conflict-header panel">
        <div className="conflict-header-top">
          <span className="mono conflict-id">{conflict.id.toUpperCase()}</span>
          <SeverityBadge severity={conflict.severity} />
          <span className="badge tone-neutral">{conflict.conflictType}</span>
          <ConflictStatusBadge status={conflict.status} />
        </div>
        <h2 className="conflict-summary">{conflict.summary}</h2>
        <p className="muted conflict-sub">
          Flagged {formatDateTime(conflict.createdAt)} · current status:{" "}
          {conflictStatusLabel(conflict.status)}
          {conflict.status !== "unresolved" &&
            " — a new decision may be recorded; the prior decision remains in history."}
        </p>
      </header>

      <details className="review-guide panel">
        <summary>Decision protocol for this conflict</summary>
        <ol>
          <li>Compare each exact quote against the original source and citation.</li>
          <li>Separate factual disagreement from source-reliability concerns.</li>
          <li>Preserve or escalate uncertainty rather than resolving it silently.</li>
          <li>Record reasoning that explains the controlling evidence and report effect.</li>
        </ol>
        <p className="muted">
          Current matter role: <strong>{matterRole ?? "not assigned"}</strong>.
          {conflict.severity === "critical" &&
            " Critical dispositive decisions require Senior Aviation Counsel."}
        </p>
      </details>

      <div className="claims-grid">
        <ClaimComparisonCard
          label="Claim A"
          claim={claimA}
          source={sourceA}
          selected={focusedId === claimA.id}
          onFocus={() => selectClaim(claimA.id)}
        />
        <ClaimComparisonCard
          label="Claim B"
          claim={claimB}
          source={sourceB}
          selected={focusedId === claimB.id}
          onFocus={() => selectClaim(claimB.id)}
        />
      </div>

      <div className="original-evidence-actions" aria-label="Original evidence">
        <span className="muted">Verify without leaving the review:</span>
        <button
          type="button"
          className="btn-secondary compact"
          disabled={!canOpenSourceA}
          title={
            canOpenSourceA
              ? `Open ${sourceA.originalFilename}`
              : "The original file is not stored for this legacy source."
          }
          onClick={() => setOpenEvidenceFor("a")}
        >
          Open Claim A original
        </button>
        <button
          type="button"
          className="btn-secondary compact"
          disabled={!canOpenSourceB}
          title={
            canOpenSourceB
              ? `Open ${sourceB.originalFilename}`
              : "The original file is not stored for this legacy source."
          }
          onClick={() => setOpenEvidenceFor("b")}
        >
          Open Claim B original
        </button>
        {!canOpenSourceA && !canOpenSourceB && (
          <span className="muted">
            These legacy demo sources predate stored originals; uploaded evidence opens here.
          </span>
        )}
      </div>

      <ReviewDecisionForm
        key={conflict.id}
        conflict={conflict}
        claimA={claimA}
        claimB={claimB}
        sourceA={sourceA}
        sourceB={sourceB}
      />

      <div className="panel-pair">
        <DecisionHistory decisions={conflictDecisions} />
        <ReportImpactPreview claims={[claimA, claimB]} />
      </div>

      {openEvidenceFor === "a" && (
        <OriginalEvidenceDialog
          claim={claimA}
          source={sourceA}
          onClose={() => setOpenEvidenceFor(null)}
        />
      )}
      {openEvidenceFor === "b" && (
        <OriginalEvidenceDialog
          claim={claimB}
          source={sourceB}
          onClose={() => setOpenEvidenceFor(null)}
        />
      )}
    </div>
  );
}
