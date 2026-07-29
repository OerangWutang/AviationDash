import { useStore } from "../../state/store";
import { AuditTrailItem } from "../AuditTrailItem";

export function AuditView() {
  const { state } = useStore();

  return (
    <div className="view-stack">
      <header className="view-header">
        <h2>Audit history</h2>
        <p className="muted">
          Append-only record of every ingestion, designation, conflict, and decision —
          newest first. Nothing in this trail can be edited or removed.
        </p>
      </header>
      <div className="panel">
        <ul className="audit-list">
          {state.auditEvents.map((event) => (
            <AuditTrailItem key={event.id} event={event} />
          ))}
        </ul>
      </div>
    </div>
  );
}
