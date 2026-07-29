import { useEffect, useState } from "react";

import { ApiError, getCaseSourcePages } from "../api/client";
import type { SourcePage } from "../domain/types";
import { describePageFailure } from "./QuoteVerificationBadge";

const PAGE_SIZE = 20;

/** Browse a document's extracted pages and pick one to cite.
 *
 *  Failed pages are shown, not hidden. A reviewer who cannot see that page 12
 *  was unreadable has no way to know why a quote from it needs attestation —
 *  and might assume the page was blank.
 */
export function SourcePageBrowser({
  caseId,
  sourceId,
  selectedPageId,
  onSelect,
}: {
  caseId: string;
  sourceId: string;
  selectedPageId: string | null;
  onSelect: (page: SourcePage | null) => void;
}) {
  const [pages, setPages] = useState<SourcePage[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getCaseSourcePages(caseId, sourceId, { offset, limit: PAGE_SIZE })
      .then((response) => {
        if (cancelled) return;
        setPages(response.pages);
        setTotal(response.total);
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setPages([]);
        setError(
          caught instanceof ApiError
            ? caught.message
            : "Could not load pages for this document.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [caseId, sourceId, offset]);

  if (error) {
    return (
      <p className="page-browser__error" role="alert">
        {error}
      </p>
    );
  }

  return (
    <div className="page-browser">
      <div className="page-browser__header">
        <span>
          {total} extracted page{total === 1 ? "" : "s"}
        </span>
        {total > PAGE_SIZE && (
          <span className="page-browser__paging">
            <button
              type="button"
              disabled={offset === 0 || loading}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Previous
            </button>
            <span>
              {offset + 1}–{Math.min(offset + PAGE_SIZE, total)}
            </span>
            <button
              type="button"
              disabled={offset + PAGE_SIZE >= total || loading}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </button>
          </span>
        )}
      </div>

      {loading && <p className="page-browser__hint">Loading pages…</p>}

      <ul className="page-browser__list">
        {pages.map((page) => {
          const failure = describePageFailure(page.failureCode, page.failureDetail);
          const selected = page.id === selectedPageId;
          return (
            <li key={page.id}>
              <button
                type="button"
                className={`page-browser__page${selected ? " page-browser__page--selected" : ""}`}
                aria-pressed={selected}
                onClick={() => onSelect(selected ? null : page)}
              >
                <span className="page-browser__number">Page {page.pageNumber}</span>
                <span className="page-browser__method">
                  {page.extractionMethod === "native" && "Native text"}
                  {page.extractionMethod === "ocr" &&
                    `OCR${page.ocrConfidence != null ? ` · ${page.ocrConfidence}% confidence` : ""}`}
                  {page.extractionMethod === "failed" && "Unreadable"}
                </span>
                {page.textTruncated && (
                  <span className="page-browser__flag">
                    Text was cut short — a quote not found here may still be on the page
                  </span>
                )}
                {failure && <span className="page-browser__failure">{failure}</span>}
                {page.text && (
                  <span className="page-browser__preview">
                    {page.text.slice(0, 240)}
                    {page.text.length > 240 ? "…" : ""}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>

      {!loading && pages.length === 0 && (
        <p className="page-browser__hint">This document has no extracted pages.</p>
      )}
    </div>
  );
}
