import type { ExtractionMethod, QuoteVerification } from "../domain/types";

/** How a claim's quote stands up, stated precisely.
 *
 *  The method is always named next to an exact match. An OCR match proves the
 *  quote appears in Tesseract's *interpretation* of a page image — not,
 *  independently, that the page says it. Rendering both as a bare "Verified"
 *  would present two different strengths of evidence as one, which is exactly
 *  the elision this feature exists to prevent.
 */
export function QuoteVerificationBadge({
  verification,
  extractionMethod,
  ocrConfidence,
}: {
  verification: QuoteVerification | undefined;
  extractionMethod?: ExtractionMethod | null;
  ocrConfidence?: number | null;
}) {
  if (!verification) return null;

  const detail = describe(verification, extractionMethod, ocrConfidence);
  return (
    <span
      className={`quote-verification quote-verification--${verification.replace(/_/g, "-")}`}
      title={detail.title}
    >
      <span aria-hidden="true">{detail.icon}</span> {detail.label}
    </span>
  );
}

function describe(
  verification: QuoteVerification,
  method?: ExtractionMethod | null,
  ocrConfidence?: number | null,
): { icon: string; label: string; title: string } {
  switch (verification) {
    case "verified_exact": {
      const isOcr = method === "ocr";
      return {
        icon: "✓",
        label: isOcr ? "Exact match — OCR text" : "Exact match — native PDF text",
        title: isOcr
          ? `This quote appears in the text OCR read from the page image${
              ocrConfidence != null ? ` (${ocrConfidence}% confidence)` : ""
            }. That is the engine's reading of the scan, not an independent confirmation of the page itself.`
          : "This quote appears verbatim in the document's own embedded text layer.",
      };
    }
    case "manually_verified":
      return {
        icon: "☑",
        label: "Verified by reviewer",
        title:
          "Automatic checking could not confirm this quote. A second Senior Aviation Counsel confirmed it against the original document and recorded why.",
      };
    case "manual_verification_required":
      return {
        icon: "!",
        label: "Needs verification",
        title:
          "Nothing has confirmed this quote yet: the page could not be read, its text was cut short, or OCR confidence was too low. The claim cannot be used in a report until a reviewer attests to it.",
      };
    case "legacy_unverified":
      return {
        icon: "–",
        label: "Not checked",
        title:
          "This source predates document ingestion, so there is no extracted page text to check the quote against.",
      };
  }
}

/** Plain-language reason a page has no usable text.
 *
 *  Kept human-readable on purpose: "pixel_limit" tells a reviewer nothing, and
 *  a reviewer who cannot tell why a page failed cannot judge whether to trust
 *  a claim that cites it.
 */
export function describePageFailure(
  failureCode: string | null,
  failureDetail: string | null,
): string | null {
  if (!failureCode) return null;
  switch (failureCode) {
    case "total_text_budget_exhausted":
      return "Not processed — the document's overall text limit was reached before this page.";
    case "ocr_unavailable":
      return "This page is a scan and the server has no OCR engine installed, so its text could not be read.";
    case "ocr_timeout":
    case "raster_timeout":
      return "This page took too long to read and was stopped.";
    case "pixel_limit":
    case "invalid_media_box":
      return "This page's dimensions are outside what can safely be rendered.";
    case "native_extract_error":
    case "page_parse_error":
      return "This page could not be read from the document.";
    default:
      return failureDetail ? `Could not read this page (${failureDetail}).` : "Could not read this page.";
  }
}
