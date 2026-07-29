import type { Claim } from "../domain/types";
import { computeSectionImpact } from "../domain/report";
import { useStore } from "../state/store";
import { SectionStatusBadge } from "./StatusBadge";

/**
 * Report sections citing any of the given claims, with live usability status.
 */
export function ReportImpactPreview({ claims }: { claims: Claim[] }) {
  const { state, selectView } = useStore();
  const claimIds = new Set(claims.map((c) => c.id));
  const affected = state.reportSections.filter((s) =>
    s.claimIds.some((id) => claimIds.has(id)),
  );

  return (
    <section className="panel" aria-label="Report impact">
      <h3 className="section-title">Report impact</h3>
      {affected.length === 0 ? (
        <p className="muted empty-note">No report paragraph cites these claims yet.</p>
      ) : (
        <ul className="impact-list">
          {affected.map((section) => {
            const impact = computeSectionImpact(section, state.claims, state.conflicts);
            return (
              <li key={section.id} className="impact-item">
                <div className="impact-head">
                  <span>
                    <strong>{section.title}</strong>{" "}
                    <span className="mono muted">¶ {section.paragraphRef}</span>
                  </span>
                  <SectionStatusBadge status={impact.status} />
                </div>
                <p className="impact-note">{impact.note}</p>
              </li>
            );
          })}
        </ul>
      )}
      <button type="button" className="btn-link" onClick={() => selectView("reports")}>
        Open full report view →
      </button>
    </section>
  );
}
