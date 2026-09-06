import { useId, useRef, useState } from "react";

import { MATTER_TYPES, validateNewMatter, type NewMatterInput } from "../domain/matters";
import { useStore } from "../state/store";

const EMPTY: NewMatterInput = {
  name: "",
  aircraft: "",
  accidentDate: "",
  location: "",
  matterType: "wrongful_death",
  docketRef: "",
};

/** Open a new matter.
 *
 *  Only offered to a global Senior Aviation Counsel: a matter that does not
 *  exist yet has no membership to authorize against, so this sits on the same
 *  plane as reviewer administration. The server enforces it; the UI simply
 *  does not present an action the reviewer cannot take.
 */
export function NewMatterForm() {
  const { state, createMatter } = useStore();
  const formId = useId();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<NewMatterInput>(EMPTY);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState<string | null>(null);

  const reviewer = state.sessionReviewer;
  if (state.dataMode !== "server" || reviewer?.role !== "Senior Aviation Counsel") {
    return null;
  }

  const blocker = validateNewMatter(draft);

  const set = (field: keyof NewMatterInput) => (value: string) => {
    setDraft((previous) => ({ ...previous, [field]: value }));
    setError(null);
  };

  if (!open) {
    return (
      <div className="new-matter">
        <button type="button" className="btn-secondary" onClick={() => setOpen(true)}>
          Open a new matter
        </button>
      </div>
    );
  }

  return (
    <form
      className="new-matter new-matter--open"
      onSubmit={async (event) => {
        event.preventDefault();
        if (blocker || busyRef.current) return;
        busyRef.current = true;
        setBusy(true);
        try {
          const outcome = await createMatter(draft);
          if (outcome.ok) {
            setDraft(EMPTY);
            setOpen(false);
          } else {
            setError(outcome.error);
          }
        } finally {
          busyRef.current = false;
          setBusy(false);
        }
      }}
    >
      <h3>Open a new matter</h3>
      <p className="muted">
        You become its Senior Aviation Counsel. The matter starts empty and open,
        with its own audit trail.
      </p>

      <label className="field" htmlFor={`${formId}-name`}>
        <span>Matter name</span>
        <input
          id={`${formId}-name`}
          value={draft.name}
          maxLength={200}
          disabled={busy}
          placeholder="Trans-Pacific 118 Runway Excursion"
          onChange={(event) => set("name")(event.target.value)}
        />
      </label>

      <label className="field" htmlFor={`${formId}-aircraft`}>
        <span>Aircraft</span>
        <input
          id={`${formId}-aircraft`}
          value={draft.aircraft}
          maxLength={200}
          disabled={busy}
          placeholder="Boeing 777-300ER (N881TP)"
          onChange={(event) => set("aircraft")(event.target.value)}
        />
      </label>

      <div className="flag-grid">
        <label className="field" htmlFor={`${formId}-date`}>
          <span>Accident date</span>
          <input
            id={`${formId}-date`}
            type="date"
            value={draft.accidentDate}
            disabled={busy}
            onChange={(event) => set("accidentDate")(event.target.value)}
          />
        </label>
        <label className="field" htmlFor={`${formId}-location`}>
          <span>Location</span>
          <input
            id={`${formId}-location`}
            value={draft.location}
            maxLength={200}
            disabled={busy}
            placeholder="Anchorage, Alaska"
            onChange={(event) => set("location")(event.target.value)}
          />
        </label>
      </div>

      <div className="flag-grid">
        <label className="field" htmlFor={`${formId}-type`}>
          <span>Matter type</span>
          <select
            id={`${formId}-type`}
            value={draft.matterType}
            disabled={busy}
            onChange={(event) => set("matterType")(event.target.value)}
          >
            {MATTER_TYPES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field" htmlFor={`${formId}-docket`}>
          <span>Docket reference</span>
          <input
            id={`${formId}-docket`}
            value={draft.docketRef}
            maxLength={120}
            disabled={busy}
            placeholder="DCA26MA004"
            onChange={(event) => set("docketRef")(event.target.value)}
          />
        </label>
      </div>

      {blocker && (
        <p className="muted" role="status">
          {blocker}
        </p>
      )}
      {error && (
        <p className="save-error" role="alert">
          {error}
        </p>
      )}

      <div className="new-matter__actions">
        <button type="submit" className="btn-primary" disabled={blocker !== null || busy}>
          {busy ? "Opening…" : "Open matter"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={busy}
          onClick={() => {
            setOpen(false);
            setDraft(EMPTY);
            setError(null);
          }}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
