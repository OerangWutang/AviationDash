import { useRef, useState } from "react";
import type { ReportSection } from "../domain/types";
import { computeSectionImpact } from "../domain/report";
import {
  MIN_SECTION_TEXT_LENGTH,
  validateReportSection,
} from "../domain/reportSections";
import { useStore } from "../state/store";
import { SectionStatusBadge, EligibilityBadge } from "./StatusBadge";

function truncate(text: string, max = 110): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

export function SectionEditor({
  existing,
  onClose,
}: {
  existing: ReportSection | null;
  onClose: () => void;
}) {
  const { state, saveReportSection } = useStore();
  const [title, setTitle] = useState(existing?.title ?? "");
  const [paragraphRef, setParagraphRef] = useState(existing?.paragraphRef ?? "");
  const [text, setText] = useState(existing?.text ?? "");
  const [claimIds, setClaimIds] = useState<string[]>(existing?.claimIds ?? []);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);

  const input = { title, paragraphRef, text, claimIds };
  const blocker = validateReportSection(
    input,
    state.claims,
    state.reportSections,
    existing?.id ?? null,
  );
  const liveImpact =
    claimIds.length > 0
      ? computeSectionImpact(
          { claimIds, paragraphRef: paragraphRef || "—" },
          state.claims,
          state.conflicts,
        )
      : null;

  const claims = [...state.claims.values()].sort((a, b) => a.id.localeCompare(b.id));

  const toggleClaim = (claimId: string) => {
    setSaveError(null);
    setClaimIds((current) =>
      current.includes(claimId)
        ? current.filter((id) => id !== claimId)
        : [...current, claimId],
    );
  };

  const handleSave = async () => {
    if (savingRef.current || blocker !== null) return;
    savingRef.current = true;
    setSaving(true);
    try {
      const outcome = await saveReportSection(input, existing?.id);
      if (outcome.ok) {
        onClose();
      } else {
        setSaveError(outcome.error);
      }
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  return (
    <section className="panel section-editor" aria-label="Section editor">
      <h3 className="section-title">
        {existing ? `Revise section — ¶ ${existing.paragraphRef}` : "Draft new section"}
      </h3>
      <p className="muted new-claim-note">
        A section cannot be saved without citing at least one claim; drafting and every
        revision are recorded in the audit trail with the section's usability status.
      </p>

      <div className="flag-grid">
        <label className="field grow">
          <span>Section title</span>
          <input
            type="text"
            value={title}
            placeholder="e.g. Engine Performance"
            onChange={(e) => {
              setTitle(e.target.value);
              setSaveError(null);
            }}
          />
        </label>
        <label className="field">
          <span>Paragraph reference</span>
          <input
            type="text"
            value={paragraphRef}
            placeholder="e.g. ENG-1"
            onChange={(e) => setParagraphRef(e.target.value)}
          />
        </label>
      </div>

      <label className="field">
        <span>
          Section text{" "}
          <span className="muted">
            (min {MIN_SECTION_TEXT_LENGTH} characters; carry inline citations like
            “[FDR Data Readout, p. 44]”)
          </span>
        </span>
        <textarea
          rows={4}
          value={text}
          placeholder="Draft report paragraph with inline source citations."
          onChange={(e) => setText(e.target.value)}
        />
      </label>

      <fieldset className="cite-picker-wrap">
        <legend>
          Cited claims <span className="muted">({claimIds.length} selected)</span>
        </legend>
        <ul className="cite-picker">
          {claims.map((claim) => (
            <li key={claim.id} className="cite-row">
              <input
                type="checkbox"
                checked={claimIds.includes(claim.id)}
                aria-label={`Cite ${claim.id}`}
                onChange={() => toggleClaim(claim.id)}
              />
              <span className="mono muted cite-row-id">{claim.id}</span>
              <EligibilityBadge eligibility={claim.reportEligibility} />
              <span className="cite-row-text">{truncate(claim.text)}</span>
            </li>
          ))}
        </ul>
      </fieldset>

      {liveImpact && (
        <div className="impact-item editor-impact">
          <div className="impact-head">
            <span>Usability with these citations</span>
            <SectionStatusBadge status={liveImpact.status} />
          </div>
          <p className="impact-note">{liveImpact.note}</p>
        </div>
      )}

      <div className="save-row">
        <button
          type="button"
          className="btn-primary"
          disabled={blocker !== null || saving}
          onClick={() => void handleSave()}
        >
          {saving ? "Saving…" : existing ? "Save revision" : "Save section"}
        </button>
        <button type="button" className="btn-secondary" disabled={saving} onClick={onClose}>
          Cancel
        </button>
        {blocker && <span className="save-blocker">{blocker}</span>}
      </div>
      {saveError && <p className="form-error">{saveError}</p>}
    </section>
  );
}
