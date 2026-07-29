import { useState } from "react";
import type { ConflictSeverity, ConflictType } from "../../domain/types";
import { MIN_SUMMARY_LENGTH } from "../../domain/claims";
import { useStore } from "../../state/store";
import { formatDateTime, formatConfidence } from "../../lib/format";
import { ClaimStatusBadge, EligibilityBadge, SeverityDot } from "../StatusBadge";
import { PrivilegeBadge } from "../PrivilegeBadge";

const CONFLICT_TYPES: ConflictType[] = [
  "timeline",
  "factual",
  "interpretation",
  "source_reliability",
];
const SEVERITIES: ConflictSeverity[] = ["critical", "high", "medium", "low"];

export function ClaimsView() {
  const { state, selectClaim, selectConflict, flagConflict } = useStore();
  const [selected, setSelected] = useState<string[]>([]);
  const [conflictType, setConflictType] = useState<ConflictType>("factual");
  const [severity, setSeverity] = useState<ConflictSeverity>("medium");
  const [summary, setSummary] = useState("");
  const [flagError, setFlagError] = useState<string | null>(null);

  const claims = [...state.claims.values()].sort((a, b) => a.id.localeCompare(b.id));

  const toggleSelected = (claimId: string) => {
    setFlagError(null);
    setSelected((current) =>
      current.includes(claimId)
        ? current.filter((id) => id !== claimId)
        : [...current, claimId],
    );
  };

  const summaryShortfall = Math.max(0, MIN_SUMMARY_LENGTH - summary.trim().length);
  let blocker: string | null = null;
  if (selected.length !== 2) {
    blocker = `Select exactly two claims below to flag a conflict (${selected.length} selected).`;
  } else if (summaryShortfall > 0) {
    blocker = `A conflict summary is required — ${summaryShortfall} more character${
      summaryShortfall === 1 ? "" : "s"
    } needed.`;
  }

  const handleFlag = async () => {
    if (selected.length !== 2) return;
    const outcome = await flagConflict({
      claimAId: selected[0],
      claimBId: selected[1],
      conflictType,
      severity,
      summary,
    });
    if (outcome.ok) {
      // The store jumps to the new conflict in the review workspace.
      setSelected([]);
      setSummary("");
      setFlagError(null);
    } else {
      setFlagError(outcome.error);
    }
  };

  return (
    <div className="view-stack">
      <header className="view-header">
        <h2>Claims ledger</h2>
        <p className="muted">
          Every claim in the case, each traceable to a source. Select two claims that
          contradict each other to flag a conflict — both claims become disputed and
          report-blocked until the conflict is reviewed.
        </p>
      </header>

      <section className="panel" aria-label="Flag conflict">
        <h3 className="section-title">Flag a conflict</h3>
        <div className="flag-grid">
          <label className="field">
            <span>Conflict type</span>
            <select
              value={conflictType}
              onChange={(e) => setConflictType(e.target.value as ConflictType)}
            >
              {CONFLICT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Severity</span>
            <select
              value={severity}
              onChange={(e) => setSeverity(e.target.value as ConflictSeverity)}
            >
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="field grow">
            <span>
              Summary <span className="muted">(required, min {MIN_SUMMARY_LENGTH} characters)</span>
            </span>
            <input
              type="text"
              value={summary}
              placeholder="What do these two claims disagree about?"
              onChange={(e) => {
                setSummary(e.target.value);
                setFlagError(null);
              }}
            />
          </label>
        </div>
        <div className="save-row">
          <button
            type="button"
            className="btn-primary"
            disabled={blocker !== null}
            onClick={() => void handleFlag()}
          >
            Flag conflict
          </button>
          {blocker && <span className="save-blocker">{blocker}</span>}
        </div>
        {flagError && <p className="form-error">{flagError}</p>}
      </section>

      <section className="panel" aria-label="All claims">
        <h3 className="section-title">All claims ({claims.length})</h3>
        <ul className="claims-list">
          {claims.map((claim) => {
            const source = state.sources.get(claim.sourceDocumentId);
            const isSelected = selected.includes(claim.id);
            return (
              <li key={claim.id} className={`claim-row${isSelected ? " selected" : ""}`}>
                <input
                  type="checkbox"
                  className="claim-check"
                  checked={isSelected}
                  aria-label={`Select ${claim.id}`}
                  onChange={() => toggleSelected(claim.id)}
                />
                <div className="claim-row-body">
                  <div className="claim-row-head">
                    <span className="mono muted claim-row-id">{claim.id}</span>
                    <span className="badge-row">
                      <ClaimStatusBadge status={claim.status} />
                      <PrivilegeBadge status={claim.privilegeStatus} />
                      <EligibilityBadge eligibility={claim.reportEligibility} />
                    </span>
                  </div>
                  <button
                    type="button"
                    className="claim-row-text"
                    title="Focus this claim in the Evidence Rail"
                    onClick={() => selectClaim(claim.id)}
                  >
                    {claim.text}
                  </button>
                  <p className="claim-row-meta muted">
                    {source?.title ?? claim.sourceDocumentId} · {claim.pageRef} ·
                    confidence {formatConfidence(claim.confidence)} · updated{" "}
                    <span className="mono">{formatDateTime(claim.updatedAt)}</span>
                  </p>
                  {claim.relatedConflictIds.length > 0 && (
                    <div className="claim-row-conflicts">
                      {claim.relatedConflictIds.map((conflictId) => {
                        const conflict = state.conflicts.get(conflictId);
                        if (!conflict) return null;
                        return (
                          <button
                            key={conflictId}
                            type="button"
                            className="claim-chip"
                            title={conflict.summary}
                            onClick={() => selectConflict(conflictId)}
                          >
                            <SeverityDot severity={conflict.severity} />
                            <span className="mono">{conflictId.toUpperCase()}</span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}
