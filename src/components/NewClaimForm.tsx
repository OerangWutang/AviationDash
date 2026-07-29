import { useState } from "react";
import type { EvidenceQuality, SourceDocument, SourcePage } from "../domain/types";
import { WITHHOLDING_PRIVILEGE } from "../domain/types";
import {
  MIN_CLAIM_TEXT_LENGTH,
  MIN_QUOTE_LENGTH,
  quoteAppearsOnPage,
} from "../domain/claims";
import { useStore } from "../state/store";
import { privilegeLabel } from "../lib/format";
import { SourcePageBrowser } from "./SourcePageBrowser";
import { describePageFailure } from "./QuoteVerificationBadge";

const QUALITIES: EvidenceQuality[] = [
  "primary",
  "corroborated",
  "secondary",
  "uncorroborated",
];

export function NewClaimForm({ source }: { source: SourceDocument }) {
  const { state, createClaim, selectView, selectClaim } = useStore();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [pageRef, setPageRef] = useState("");
  const [quote, setQuote] = useState("");
  const [confidencePct, setConfidencePct] = useState(70);
  const [quality, setQuality] = useState<EvidenceQuality>("secondary");
  const [error, setError] = useState<string | null>(null);
  const [savedClaimId, setSavedClaimId] = useState<string | null>(null);
  const [citedPage, setCitedPage] = useState<SourcePage | null>(null);

  const reviewer = state.reviewers.find((r) => r.id === state.activeReviewerId);
  const withholding = WITHHOLDING_PRIVILEGE.includes(source.privilegeStatus);
  const ingested = source.isIngested === true;

  let blocker: string | null = null;
  if (text.trim().length < MIN_CLAIM_TEXT_LENGTH) {
    blocker = `Claim text required (min ${MIN_CLAIM_TEXT_LENGTH} characters).`;
  } else if (pageRef.trim().length === 0) {
    blocker = "A page/section reference is required.";
  } else if (quote.trim().length < MIN_QUOTE_LENGTH) {
    blocker = `A supporting quote is required (min ${MIN_QUOTE_LENGTH} characters).`;
  } else if (ingested && citedPage === null) {
    blocker = "Select the page this quote comes from.";
  }

  // Advisory only — the server re-checks and is authoritative. Shown so a
  // reviewer finds out before saving, not through a rejected submission.
  const quoteFound =
    citedPage && citedPage.extractionMethod !== "failed" && quote.trim().length > 0
      ? quoteAppearsOnPage(quote, citedPage.text)
      : null;

  const handleSave = async () => {
    const outcome = await createClaim({
      sourceDocumentId: source.id,
      text,
      pageRef,
      quote,
      confidence: confidencePct / 100,
      evidenceQuality: quality,
      sourcePageExtractionId: citedPage?.id ?? null,
    });
    if (outcome.ok) {
      setText("");
      setPageRef("");
      setQuote("");
      setCitedPage(null);
      setError(null);
      setSavedClaimId(outcome.claim.id);
    } else {
      setError(outcome.error);
    }
  };

  if (!open) {
    return (
      <div className="new-claim">
        <button type="button" className="btn-secondary" onClick={() => setOpen(true)}>
          Extract claim from this document
        </button>
        {savedClaimId && (
          <p className="save-confirm" role="status">
            Claim <span className="mono">{savedClaimId}</span> extracted and recorded in
            the audit trail.{" "}
            <button
              type="button"
              className="btn-link inline"
              onClick={() => {
                selectClaim(savedClaimId);
                selectView("claims");
              }}
            >
              View in claims ledger →
            </button>
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="new-claim panel-inset-form">
      <h4 className="rail-title">New claim from “{source.title}”</h4>
      <p className="muted new-claim-note">
        Privilege is inherited from the source:{" "}
        <strong>{privilegeLabel(source.privilegeStatus)}</strong>
        {withholding &&
          " — this claim will be born privileged and withheld from report use"}
        . Extraction is recorded in the audit trail
        {reviewer ? ` as ${reviewer.name}` : ""}.
      </p>
      <label className="field">
        <span>
          Claim text <span className="muted">(min {MIN_CLAIM_TEXT_LENGTH} characters)</span>
        </span>
        <textarea
          rows={2}
          value={text}
          placeholder="The factual assertion this document supports."
          onChange={(e) => {
            setText(e.target.value);
            setError(null);
          }}
        />
      </label>
      {ingested && (
        <div className="field">
          <span>Page this quote comes from</span>
          <p className="muted">
            The quote is checked against this page's extracted text before the
            claim is saved.
          </p>
          <SourcePageBrowser
            caseId={source.caseId}
            sourceId={source.id}
            selectedPageId={citedPage?.id ?? null}
            onSelect={(page) => {
              setCitedPage(page);
              setError(null);
              // Pre-fill only; the reviewer owns the real citation, which
              // routinely differs from the PDF's own page index.
              if (page && pageRef.trim() === "") setPageRef(page.defaultPageRef);
            }}
          />
          {citedPage && citedPage.extractionMethod === "failed" && (
            <p className="muted" role="status">
              {describePageFailure(citedPage.failureCode, citedPage.failureDetail)}{" "}
              You can still cite this page, but the claim will need a second
              reviewer to confirm the quote against the original document before
              it can be used in a report.
            </p>
          )}
          {quoteFound === false && citedPage && !citedPage.textTruncated && (
            <p className="save-error" role="alert">
              This quote was not found in page {citedPage.pageNumber}. Check the
              page, or correct the quote to match the document.
            </p>
          )}
          {quoteFound === false && citedPage?.textTruncated && (
            <p className="muted" role="status">
              This quote was not found in the text kept for page{" "}
              {citedPage.pageNumber}, but that page's text was cut short, so the
              quote may still be on it. The claim will need reviewer
              confirmation.
            </p>
          )}
          {quoteFound === true && (
            <p className="save-confirm" role="status">
              Found on page {citedPage?.pageNumber} (
              {citedPage?.extractionMethod === "ocr"
                ? "OCR-extracted text"
                : "native PDF text"}
              ).
            </p>
          )}
        </div>
      )}
      <div className="flag-grid">
        <label className="field">
          <span>Page / section reference</span>
          <input
            type="text"
            value={pageRef}
            placeholder="p. 12, ¶3"
            onChange={(e) => setPageRef(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Confidence (%)</span>
          <input
            type="number"
            min={0}
            max={100}
            value={confidencePct}
            onChange={(e) =>
              setConfidencePct(Math.min(100, Math.max(0, Number(e.target.value))))
            }
          />
        </label>
        <label className="field">
          <span>Evidence quality</span>
          <select
            value={quality}
            onChange={(e) => setQuality(e.target.value as EvidenceQuality)}
          >
            {QUALITIES.map((q) => (
              <option key={q} value={q}>
                {q}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="field">
        <span>
          Supporting quote <span className="muted">(verbatim, min {MIN_QUOTE_LENGTH} characters)</span>
        </span>
        <textarea
          rows={2}
          value={quote}
          placeholder="Exact language from the source at the cited location."
          onChange={(e) => setQuote(e.target.value)}
        />
      </label>
      <div className="save-row">
        <button
          type="button"
          className="btn-primary"
          disabled={blocker !== null}
          onClick={() => void handleSave()}
        >
          Save claim
        </button>
        <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>
          Close
        </button>
        {blocker && <span className="save-blocker">{blocker}</span>}
      </div>
      {error && <p className="form-error">{error}</p>}
      {savedClaimId && (
        <p className="save-confirm" role="status">
          Claim <span className="mono">{savedClaimId}</span> extracted and recorded in
          the audit trail.{" "}
          <button
            type="button"
            className="btn-link inline"
            onClick={() => {
              selectClaim(savedClaimId);
              selectView("claims");
            }}
          >
            View in claims ledger →
          </button>
        </p>
      )}
    </div>
  );
}
