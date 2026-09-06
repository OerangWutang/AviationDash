import { useEffect, useRef, useState } from "react";

import { ApiError, fetchSourcePdf, getCaseSourcePages } from "../api/client";
import type { Claim, SourceDocument } from "../domain/types";
import { truncateHash } from "../lib/format";
import { QuoteVerificationBadge } from "./QuoteVerificationBadge";

async function findCitedPageNumber(
  claim: Claim,
  source: SourceDocument,
): Promise<number | null> {
  if (!claim.sourcePageExtractionId || !source.pageCount) return null;
  const pageSize = 100;
  for (let offset = 0; offset < source.pageCount; offset += pageSize) {
    const result = await getCaseSourcePages(source.caseId, source.id, {
      offset,
      limit: pageSize,
    });
    const page = result.pages.find((item) => item.id === claim.sourcePageExtractionId);
    if (page) return page.pageNumber;
    if (offset + result.pages.length >= result.total) break;
  }
  return null;
}

export function OriginalEvidenceDialog({
  claim,
  source,
  onClose,
}: {
  claim: Claim;
  source: SourceDocument;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [pageNumber, setPageNumber] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
    closeRef.current?.focus();
    return () => {
      if (dialog.open && typeof dialog.close === "function") dialog.close();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let nextUrl: string | null = null;
    setError(null);
    Promise.all([
      fetchSourcePdf(source.caseId, source.id, source.contentSha256 ?? ""),
      findCitedPageNumber(claim, source),
    ])
      .then(([blob, citedPage]) => {
        if (cancelled) return;
        nextUrl = URL.createObjectURL(blob);
        setObjectUrl(nextUrl);
        setPageNumber(citedPage);
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setError(
          caught instanceof ApiError
            ? caught.message
            : "Could not open the source document.",
        );
      });
    return () => {
      cancelled = true;
      if (nextUrl) URL.revokeObjectURL(nextUrl);
    };
  }, [claim, source]);

  const viewerUrl = objectUrl
    ? `${objectUrl}${pageNumber ? `#page=${pageNumber}` : ""}`
    : null;

  return (
    <dialog
      ref={dialogRef}
      className="evidence-dialog"
      aria-labelledby="evidence-dialog-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <header className="evidence-dialog__header">
        <div>
          <p className="section-title">Original evidence</p>
          <h2 id="evidence-dialog-title">{source.title}</h2>
          <p className="muted">
            {source.originalFilename} · cited as {claim.pageRef}
            {pageNumber ? ` · PDF page ${pageNumber}` : ""}
          </p>
        </div>
        <button
          ref={closeRef}
          type="button"
          className="btn-secondary"
          onClick={onClose}
        >
          Close evidence
        </button>
      </header>

      <div className="evidence-dialog__claim">
        <div>
          <strong>Claim under review</strong>
          <p>{claim.text}</p>
        </div>
        <blockquote className="quote-block">“{claim.quote}”</blockquote>
        <QuoteVerificationBadge verification={claim.quoteVerification} />
        <span className="mono muted" title={source.contentSha256}>
          Source SHA-256 {truncateHash(source.contentSha256 ?? "")}
        </span>
      </div>

      {error && (
        <p className="form-error evidence-dialog__error" role="alert">
          {error}
        </p>
      )}
      {!error && !viewerUrl && (
        <p className="muted evidence-dialog__loading" role="status">
          Verifying and loading the original PDF…
        </p>
      )}
      {viewerUrl && (
        <iframe
          className="evidence-dialog__frame"
          title={`Original evidence — ${source.title}`}
          src={viewerUrl}
        />
      )}
    </dialog>
  );
}
