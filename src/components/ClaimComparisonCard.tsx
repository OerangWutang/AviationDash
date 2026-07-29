import type { Claim, SourceDocument } from "../domain/types";
import {
  claimStatusLabel,
  evidenceQualityLabel,
  formatConfidence,
  formatDateTime,
} from "../lib/format";
import { ClaimStatusBadge, EligibilityBadge } from "./StatusBadge";
import { PrivilegeBadge } from "./PrivilegeBadge";
import { QuoteVerificationBadge } from "./QuoteVerificationBadge";

export function ClaimComparisonCard({
  label,
  claim,
  source,
  selected,
  onFocus,
}: {
  label: "Claim A" | "Claim B";
  claim: Claim;
  source: SourceDocument;
  selected: boolean;
  onFocus: () => void;
}) {
  return (
    <button
      type="button"
      className={`claim-card${selected ? " selected" : ""}`}
      aria-pressed={selected}
      onClick={onFocus}
      title="Focus this claim in the Evidence Rail"
    >
      <div className="claim-card-head">
        <span className="claim-label">{label}</span>
        <span className="badge-row">
          <ClaimStatusBadge status={claim.status} />
          <PrivilegeBadge status={claim.privilegeStatus} />
        </span>
      </div>

      <p className="claim-text">{claim.text}</p>

      <div className="claim-source">
        <span className="source-title">{source.title}</span>
        <span className="mono muted">{claim.pageRef}</span>
      </div>
      <blockquote className="quote-block">“{claim.quote}”</blockquote>
      <QuoteVerificationBadge verification={claim.quoteVerification} />

      <dl className="meta-grid claim-meta">
        <dt>Confidence</dt>
        <dd>
          <span className="confidence">
            <progress
              className="confidence-bar"
              max={1}
              value={claim.confidence}
              aria-label={`Confidence ${formatConfidence(claim.confidence)}`}
            />
            <span className="mono">{formatConfidence(claim.confidence)}</span>
          </span>
        </dd>
        <dt>Evidence quality</dt>
        <dd>{evidenceQualityLabel(claim.evidenceQuality)}</dd>
        <dt>Report eligibility</dt>
        <dd>
          <EligibilityBadge eligibility={claim.reportEligibility} />
        </dd>
        <dt>Reviewer</dt>
        <dd>{claim.reviewer ?? `— (${claimStatusLabel(claim.status)})`}</dd>
        <dt>Last updated</dt>
        <dd className="mono">{formatDateTime(claim.updatedAt)}</dd>
      </dl>

      {claim.reportEligibility === "privileged" && (
        <p className="privilege-warning">
          Privileged material — withheld from report use regardless of review outcome.
        </p>
      )}
    </button>
  );
}
