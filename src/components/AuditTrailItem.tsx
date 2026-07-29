import type { AuditEvent } from "../domain/types";
import { formatDateTime } from "../lib/format";

export function AuditTrailItem({
  event,
  compact = false,
}: {
  event: AuditEvent;
  compact?: boolean;
}) {
  return (
    <li className={`audit-item${compact ? " compact" : ""}`}>
      <div className="audit-head">
        <span className="mono audit-time">{formatDateTime(event.at)}</span>
        <span className="audit-actor">
          {event.actor} <span className="muted">· {event.actorRole}</span>
        </span>
      </div>
      <div className="audit-action">
        {event.action}
        {event.previousStatus !== null && event.newStatus !== null && (
          <span className="mono status-flow">
            {" "}
            {event.previousStatus} → {event.newStatus}
          </span>
        )}
        {event.previousStatus === null && event.newStatus !== null && (
          <span className="mono status-flow"> → {event.newStatus}</span>
        )}
      </div>
      {!compact && <p className="audit-detail">{event.detail}</p>}
      {event.reportEligibilityChanged && (
        <span className="audit-flag">report eligibility changed</span>
      )}
    </li>
  );
}
