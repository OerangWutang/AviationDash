import { useState } from "react";
import { useStore, type View } from "../state/store";
import { computeSectionImpact } from "../domain/report";
import { formatDate } from "../lib/format";
import { matchesQueueFilter, QUEUE_FILTERS, type QueueFilter } from "../lib/queueFilter";
import { ConflictStatusBadge, SeverityDot } from "./StatusBadge";
import { NewMatterForm } from "./NewMatterForm";

const NAV: { view: View; label: string }[] = [
  { view: "conflicts", label: "Conflict review" },
  { view: "claims", label: "Claims" },
  { view: "documents", label: "Documents" },
  { view: "reports", label: "Reports" },
  { view: "export", label: "Evidence packet" },
  { view: "audit", label: "Audit history" },
];

export function CaseSidebar() {
  const { state, selectView, selectConflict, resetCase } = useStore();
  const [queueFilter, setQueueFilter] = useState<QueueFilter>("all");
  const [confirmingReset, setConfirmingReset] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const { caseFile } = state;
  const conflictList = state.conflictOrder
    .map((id) => state.conflicts.get(id))
    .filter((c) => c !== undefined);
  const unresolved = conflictList.filter((c) => c.status === "unresolved").length;
  const filteredConflicts = conflictList.filter((c) =>
    matchesQueueFilter(c.status, queueFilter),
  );
  const navItems =
    state.dataMode === "server" &&
    state.sessionReviewer?.role === "Senior Aviation Counsel"
      ? [...NAV, { view: "admin" as const, label: "Reviewer admin" }]
      : NAV;
  const canResetCase = state.dataMode === "local";

  // Sections currently includable in a production packet.
  const exportable = state.reportSections.filter((section) => {
    const status = computeSectionImpact(section, state.claims, state.conflicts).status;
    const usable = status === "eligible" || status === "eligible_with_disclosure";
    const approved =
      state.dataMode !== "server" || section.approvalState === "approved";
    return usable && approved;
  }).length;

  const counts: Record<View, number> = {
    conflicts: unresolved,
    claims: state.claims.size,
    documents: state.sources.size,
    reports: state.reportSections.length,
    export: exportable,
    audit: state.auditEvents.length,
    admin: state.reviewers.length,
  };

  return (
    <aside className="sidebar" aria-label="Case context">
      <button
        type="button"
        className="sidebar-toggle"
        aria-expanded={contextOpen}
        aria-controls="case-context-panel"
        onClick={() => setContextOpen((open) => !open)}
      >
        <span>
          <strong>{caseFile.name}</strong>
          <span className="mono muted"> · {unresolved} open conflicts</span>
        </span>
        <span aria-hidden="true">{contextOpen ? "Hide" : "Show"}</span>
      </button>
      <div
        id="case-context-panel"
        className={`sidebar-body${contextOpen ? " is-open" : ""}`}
      >
      <section className="side-section">
        <h2 className="side-case-name">{caseFile.name}</h2>
        <p className="mono side-docket">{caseFile.docketRef}</p>
        <dl className="case-facts">
          <dt>Aircraft</dt>
          <dd>{caseFile.aircraft}</dd>
          <dt>Accident date</dt>
          <dd className="mono">{formatDate(caseFile.accidentDate + "T00:00:00Z")}</dd>
          <dt>Location</dt>
          <dd>{caseFile.location}</dd>
          <dt>Matter type</dt>
          <dd>{caseFile.matterType}</dd>
          <dt>Status</dt>
          <dd>{caseFile.status}</dd>
        </dl>
      </section>

      <section className="side-section">
        <NewMatterForm />
      </section>

      <nav className="side-section" aria-label="Case sections">
        <ul className="nav-list">
          {navItems.map(({ view, label }) => (
            <li key={view}>
              <button
                type="button"
                className={`nav-item${state.view === view ? " active" : ""}`}
                aria-current={state.view === view ? "page" : undefined}
                onClick={() => selectView(view)}
              >
                <span>{label}</span>
                <span
                  className={`nav-count${view === "conflicts" && unresolved > 0 ? " alert" : ""}`}
                  title={
                    view === "conflicts"
                      ? "Unresolved conflicts"
                      : view === "export"
                        ? state.dataMode === "server"
                          ? "Approved report sections currently includable in a production packet"
                          : "Report sections currently includable in a production packet"
                        : undefined
                  }
                >
                  {counts[view]}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </nav>

      <section className="side-section grow" aria-label="Review queue">
        <h3 className="section-title">Review queue</h3>
        <div className="chip-row" role="group" aria-label="Queue filter">
          {QUEUE_FILTERS.map(({ id, label }) => {
            const count = conflictList.filter((c) =>
              matchesQueueFilter(c.status, id),
            ).length;
            return (
              <button
                key={id}
                type="button"
                className={`filter-chip${queueFilter === id ? " active" : ""}`}
                aria-pressed={queueFilter === id}
                onClick={() => setQueueFilter(id)}
              >
                {label} <span className="mono">{count}</span>
              </button>
            );
          })}
        </div>
        {filteredConflicts.length === 0 && (
          <p className="muted empty-note">No conflicts match this filter.</p>
        )}
        <ul className="queue-list">
          {filteredConflicts.map((conflict) => (
            <li key={conflict.id}>
              <button
                type="button"
                className={`queue-item${
                  state.view === "conflicts" && state.selectedConflictId === conflict.id
                    ? " active"
                    : ""
                }`}
                onClick={() => selectConflict(conflict.id)}
              >
                <span className="queue-item-head">
                  <SeverityDot severity={conflict.severity} />
                  <span className="mono queue-id">{conflict.id.toUpperCase()}</span>
                  <ConflictStatusBadge status={conflict.status} />
                </span>
                <span className="queue-summary">{conflict.summary}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="side-section side-footer" aria-label="Case data location">
        <p className="muted persist-note">
          {state.dataMode === "server"
            ? "Connected to the case service — all work is recorded server-side."
            : "Working copy saved in this browser — review work survives a reload."}
        </p>
        {state.persistenceError && (
          <p className="form-error" role="alert">
            {state.persistenceError}
          </p>
        )}
        {canResetCase && confirmingReset ? (
          <div className="reset-confirm">
            <p className="reset-warning">
              Discard all review work (decisions, extracted claims, flagged conflicts,
              audit entries) and restore the sample case?
            </p>
            <div className="save-row">
              <button
                type="button"
                className="btn-danger"
                onClick={() => {
                  void resetCase().finally(() => {
                    setConfirmingReset(false);
                    setQueueFilter("all");
                  });
                }}
              >
                Confirm reset
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setConfirmingReset(false)}
              >
                Keep my work
              </button>
            </div>
          </div>
        ) : canResetCase ? (
          <button
            type="button"
            className="btn-link reset-link"
            onClick={() => setConfirmingReset(true)}
          >
            Reset sample case…
          </button>
        ) : null}
      </section>
      </div>
    </aside>
  );
}
