import type { ReviewDecision } from "../domain/types";
import { decisionLabel } from "../domain/decisions";
import { conflictStatusLabel, formatDateTime } from "../lib/format";

export function DecisionHistory({ decisions }: { decisions: ReviewDecision[] }) {
  return (
    <section className="panel" aria-label="Decision history">
      <h3 className="section-title">Decision history</h3>
      {decisions.length === 0 ? (
        <p className="muted empty-note">
          No decisions recorded for this conflict yet. The first saved decision will
          appear here with its reasoning and status change.
        </p>
      ) : (
        <ol className="history-list">
          {decisions.map((d) => (
            <li key={d.id} className="history-item">
              <div className="history-head">
                <strong>{decisionLabel(d.decisionType)}</strong>
                <span className="mono muted">{formatDateTime(d.createdAt)}</span>
              </div>
              <div className="history-reviewer">
                {d.reviewerName} <span className="muted">· {d.reviewerRole}</span>
              </div>
              <div className="mono status-flow">
                {conflictStatusLabel(d.previousStatus)} → {conflictStatusLabel(d.newStatus)}
              </div>
              <p className="history-reasoning">{d.reasoning}</p>
              <p className="history-impact">
                <span className="muted">Report impact:</span> {d.reportImpact}
              </p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
