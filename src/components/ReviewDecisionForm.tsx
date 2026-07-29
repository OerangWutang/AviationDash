import { useState } from "react";
import type { Claim, Conflict, DecisionType, SourceDocument } from "../domain/types";
import { MIN_REASONING_LENGTH, decisionLabel } from "../domain/decisions";
import { decisionPermission } from "../domain/permissions";
import { effectiveMatterRole, useStore } from "../state/store";
import { formatDateTime } from "../lib/format";

const DECISION_ORDER: { type: DecisionType; description: string }[] = [
  {
    type: "accept_claim_a",
    description: "Claim A becomes supported; Claim B is superseded and kept in the record.",
  },
  {
    type: "accept_claim_b",
    description: "Claim B becomes supported; Claim A is superseded and kept in the record.",
  },
  {
    type: "preserve_both",
    description: "Both claims stand; report use requires disclosing the conflict.",
  },
  {
    type: "mark_unresolved",
    description: "Return the conflict to the queue; report use stays blocked.",
  },
  {
    type: "escalate",
    description: "Park the conflict for senior review; cited paragraphs remain unusable.",
  },
  {
    type: "mark_source_unreliable",
    description:
      "Reject the claim backed by the unreliable source; the other claim returns to the review queue (not auto-accepted).",
  },
];

export function ReviewDecisionForm({
  conflict,
  claimA,
  claimB,
  sourceA,
  sourceB,
}: {
  conflict: Conflict;
  claimA: Claim;
  claimB: Claim;
  sourceA: SourceDocument;
  sourceB: SourceDocument;
}) {
  const { state, saveDecision } = useStore();
  const reviewer = state.reviewers.find((r) => r.id === state.activeReviewerId);
  const matterRole = effectiveMatterRole(state);

  const [decisionType, setDecisionType] = useState<DecisionType | null>(null);
  const [unreliableClaimId, setUnreliableClaimId] = useState<string | null>(null);
  const [reasoning, setReasoning] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const lastSaved = state.lastSavedDecisionId
    ? state.decisions.get(state.lastSavedDecisionId)
    : null;
  const savedHere = lastSaved && lastSaved.conflictId === conflict.id ? lastSaved : null;

  if (!reviewer || !matterRole) return null;

  const reasoningShortfall = Math.max(0, MIN_REASONING_LENGTH - reasoning.trim().length);

  let blocker: string | null = null;
  if (!decisionType) {
    blocker = "Select a decision to continue.";
  } else if (decisionType === "mark_source_unreliable" && !unreliableClaimId) {
    blocker = "Select which claim’s source is unreliable.";
  } else if (reasoningShortfall > 0) {
    blocker = `A reasoning note is required — ${reasoningShortfall} more character${
      reasoningShortfall === 1 ? "" : "s"
    } needed.`;
  }

  const handleSave = async () => {
    if (!decisionType || saving) return;
    setSaving(true);
    try {
      const outcome = await saveDecision({
        conflictId: conflict.id,
        decisionType,
        selectedClaimId:
          decisionType === "mark_source_unreliable" ? unreliableClaimId : null,
        reasoning,
      });
      if (outcome.ok) {
        setDecisionType(null);
        setUnreliableClaimId(null);
        setReasoning("");
        setSaveError(null);
      } else {
        setSaveError(outcome.error);
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="panel decision-form" aria-label="Review decision">
      <h3 className="section-title">Review decision</h3>
      <p className="muted form-context">
        Acting as <strong>{reviewer.name}</strong> · {matterRole}. Every decision is
        recorded in the audit trail with your reasoning; no decision deletes evidence.
      </p>

      <fieldset className="decision-options">
        <legend className="visually-hidden">Decision</legend>
        {DECISION_ORDER.map(({ type, description }) => {
          const permission = decisionPermission(type, matterRole, conflict);
          return (
            <label
              key={type}
              className={`decision-option${!permission.allowed ? " disabled" : ""}${
                decisionType === type ? " selected" : ""
              }`}
            >
              <input
                type="radio"
                name={`decision-${conflict.id}`}
                value={type}
                checked={decisionType === type}
                disabled={!permission.allowed}
                onChange={() => {
                  setDecisionType(type);
                  setSaveError(null);
                }}
              />
              <span className="decision-option-body">
                <span className="decision-option-label">{decisionLabel(type)}</span>
                <span className="decision-option-desc">
                  {permission.allowed ? description : permission.reason}
                </span>
              </span>
            </label>
          );
        })}
      </fieldset>

      {decisionType === "mark_source_unreliable" && (
        <fieldset className="unreliable-picker">
          <legend>Which claim’s source is unreliable?</legend>
          {[
            { claim: claimA, source: sourceA, label: "Claim A" },
            { claim: claimB, source: sourceB, label: "Claim B" },
          ].map(({ claim, source, label }) => (
            <label key={claim.id} className="unreliable-option">
              <input
                type="radio"
                name={`unreliable-${conflict.id}`}
                value={claim.id}
                checked={unreliableClaimId === claim.id}
                onChange={() => setUnreliableClaimId(claim.id)}
              />
              <span>
                {label} — <span className="source-title">{source.title}</span>
              </span>
            </label>
          ))}
        </fieldset>
      )}

      <label className="reasoning-label" htmlFor={`reasoning-${conflict.id}`}>
        Reasoning note <span className="muted">(required, min {MIN_REASONING_LENGTH} characters)</span>
      </label>
      <textarea
        id={`reasoning-${conflict.id}`}
        className="reasoning"
        rows={3}
        placeholder="State the basis for this decision — which source controls, why, and what remains open."
        value={reasoning}
        onChange={(e) => setReasoning(e.target.value)}
      />

      <div className="save-row">
        <button
          type="button"
          className="btn-primary"
          disabled={blocker !== null || saving}
          onClick={() => void handleSave()}
        >
          Save decision
        </button>
        {blocker && <span className="save-blocker">{blocker}</span>}
      </div>

      {saveError && <p className="form-error">{saveError}</p>}

      {savedHere && (
        <p className="save-confirm" role="status">
          Decision recorded: <strong>{decisionLabel(savedHere.decisionType)}</strong> by{" "}
          {savedHere.reviewerName} at {formatDateTime(savedHere.createdAt)}. Report impact:{" "}
          {savedHere.reportImpact}.
        </p>
      )}
    </section>
  );
}
