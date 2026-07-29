import { useStore } from "../state/store";
import { conflictStatusLabel, formatDateTime } from "../lib/format";
import { ClaimComparisonCard } from "./ClaimComparisonCard";
import { ConflictStatusBadge, SeverityBadge } from "./StatusBadge";
import { ReviewDecisionForm } from "./ReviewDecisionForm";
import { DecisionHistory } from "./DecisionHistory";
import { ReportImpactPreview } from "./ReportImpactPreview";

export function ConflictReviewPanel() {
  const { state, selectClaim } = useStore();
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
    </div>
  );
}
