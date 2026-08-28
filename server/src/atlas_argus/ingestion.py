"""PDF text extraction: native text layer first, OCR where that isn't real.

Two things make this module unusual for this codebase, both deliberate.

**It runs in a child process, and the parent can always kill it.** Extraction
drives two third-party binaries (Poppler, Tesseract) over attacker-supplied
input. A malformed document can hang or allocate without bound, and a hung
``pdftoppm`` is not killed by terminating the Python process that spawned it.
So the child calls ``setsid()`` and the parent kills the whole process *group*
on timeout. Address space and CPU are capped inside the child too. This is a
real containment improvement, but it is not a sandbox: the child still
inherits the parent's filesystem and environment. A separate service with no
database credentials and cgroup limits remains the stronger answer.

**A page that could not be read is a result, not an absence.** Every page
returns a row. A failed, truncated, or low-confidence page produces text that
callers must treat as unreliable, and ``failure_code`` says which. The claim
layer turns that into an explicit "needs human attestation" state rather than
letting a quote quietly go unchecked.

Text normalisation lives here too, mirrored in ``src/domain/claims.ts``: both
sides must agree on what "the same quote" means or the UI will promise matches
the server rejects.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import re
import signal
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import (
    max_extracted_text_bytes_per_page,
    max_source_page_count,
    max_total_extracted_text_bytes,
    source_child_memory_limit_bytes,
    source_ingestion_timeout_seconds,
    source_ocr_page_timeout_seconds,
)

PARSER_NAME = "pypdf"
OCR_LANGUAGES = "eng"

#: Rendering DPI for OCR. 200 is the usual floor for reliable Tesseract output
#: on 10-12pt body text; below it, character confusion rises sharply.
OCR_RENDER_DPI = 200

#: Hard ceiling on rendered pixels per page, independent of the declared page
#: size. A PDF may declare a 200000x200000pt MediaBox in a 1 KB file; at
#: 200 DPI that is a multi-terabyte raster. Checked before rendering.
MAX_RENDER_PIXELS = 40_000_000

#: A declared page larger than this (in points, ~200 inches) is not a document
#: page. Rejected without rendering.
MAX_MEDIABOX_POINTS = 14_400

#: Fraction of a page's nominal raster that must be image for the page to be
#: treated as a scan regardless of any text layer. The spike showed a scanned
#: page carrying a Bates/protective-order header reports ~73 characters of
#: native text; a length threshold alone calls that "native" and silently
#: drops the entire page body.
IMAGE_COVERAGE_SCAN_THRESHOLD = 0.5

#: Below either of these, a text layer is too thin to trust as the page's
#: content. Biased toward OCR on purpose: wrongly OCR-ing a native page costs
#: time, wrongly trusting a scan's header loses the page.
MIN_NATIVE_CHARS = 20
MIN_NATIVE_WORDS = 5

#: Bounded so a raw exception string, path, or command line never reaches a
#: client or the integrity manifest.
MAX_FAILURE_DETAIL_CHARS = 200

_RESULT_SCHEMA = "atlas_argus.extraction_result.v1"


class ExtractionError(Exception):
    """The document could not be processed at all (vs. a single bad page)."""


class UnreadableDocument(ExtractionError):
    """Not a PDF, corrupt beyond parsing, or encrypted with a real password."""


# ── normalisation ──────────────────────────────────────────────────────────

_SOFT_HYPHEN = "­"
_QUOTE_MAP = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "′": "'",
        "″": '"',
        "–": "-",
        "—": "-",
        "―": "-",
        "−": "-",
        " ": " ",
    }
)
_LINE_WRAP_HYPHEN = re.compile(r"(\w)-\s*\n\s*(\w)")
_WHITESPACE = re.compile(r"\s+")


def normalize_quote_text(value: str) -> str:
    """Fold away differences that are typography, not substance.

    PDF text extraction introduces line-wrap hyphens, ligatures, curly quotes,
    and arbitrary whitespace that a reviewer pasting a quote will not
    reproduce. Comparing raw strings would reject correct quotes constantly and
    train people to ignore the warning.

    De-hyphenation is a genuine heuristic and can be wrong: a real hyphenated
    compound broken across lines ("cross-\\ncheck") becomes "crosscheck". It is
    applied because line-wrap hyphens are far more common in extracted PDF text
    than the alternative, but it is a known imprecision, not an exact rule.

    Mirrored by ``normalizeQuoteText`` in ``src/domain/claims.ts``.
    """
    text = unicodedata.normalize("NFKC", value)
    text = text.replace(_SOFT_HYPHEN, "")
    text = _LINE_WRAP_HYPHEN.sub(r"\1\2", text)
    text = text.translate(_QUOTE_MAP)
    text = _WHITESPACE.sub(" ", text)
    return text.strip().casefold()


def quote_appears_on_page(quote: str, page_text: str) -> bool:
    normalized_quote = normalize_quote_text(quote)
    if not normalized_quote:
        return False
    return normalized_quote in normalize_quote_text(page_text)


# ── results ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PageExtraction:
    page_number: int
    text: str
    method: str  # native | ocr | failed
    ocr_confidence_bps: int | None = None
    text_truncated: bool = False
    failure_code: str | None = None
    failure_detail: str | None = None

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExtractionResult:
    page_count: int
    pages: tuple[PageExtraction, ...]
    parser_version: str
    ocr_engine_version: str
    ocr_languages: str
    config_snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def ocr_page_count(self) -> int:
        return sum(1 for page in self.pages if page.method == "ocr")

    @property
    def failed_page_count(self) -> int:
        return sum(1 for page in self.pages if page.method == "failed")


@dataclass(frozen=True)
class ExtractionLimits:
    max_pages: int
    max_text_bytes_per_page: int
    max_total_text_bytes: int
    ocr_page_timeout_seconds: int
    overall_timeout_seconds: int
    child_memory_limit_bytes: int

    def snapshot(self) -> dict[str, int]:
        """Recorded on the run so a later verification can distinguish a
        truncation from a configuration change."""
        return {
            "maxPages": self.max_pages,
            "maxTextBytesPerPage": self.max_text_bytes_per_page,
            "maxTotalTextBytes": self.max_total_text_bytes,
            "ocrPageTimeoutSeconds": self.ocr_page_timeout_seconds,
            "overallTimeoutSeconds": self.overall_timeout_seconds,
            "childMemoryLimitBytes": self.child_memory_limit_bytes,
            "ocrRenderDpi": OCR_RENDER_DPI,
            "maxRenderPixels": MAX_RENDER_PIXELS,
            "imageCoverageScanThreshold": IMAGE_COVERAGE_SCAN_THRESHOLD,
        }


def limits_from_config() -> ExtractionLimits:
    return ExtractionLimits(
        max_pages=max_source_page_count(),
        max_text_bytes_per_page=max_extracted_text_bytes_per_page(),
        max_total_text_bytes=max_total_extracted_text_bytes(),
        ocr_page_timeout_seconds=source_ocr_page_timeout_seconds(),
        overall_timeout_seconds=source_ingestion_timeout_seconds(),
        child_memory_limit_bytes=source_child_memory_limit_bytes(),
    )


# ── in-process extraction (runs inside the child) ──────────────────────────


def _safe_detail(exc: BaseException) -> str:
    """Exception text can carry absolute paths and command lines. Keep the
    type, drop the payload."""
    return type(exc).__name__[:MAX_FAILURE_DETAIL_CHARS]


def _tesseract_version() -> str:
    try:
        import pytesseract

        return f"tesseract-{pytesseract.get_tesseract_version()}"
    except Exception:  # noqa: BLE001 - absence is a normal, reportable state
        return "tesseract-unavailable"


def _image_coverage(page) -> float:
    """Raster pixels on the page as a multiple of a nominal 150 DPI page.

    ~1.0 means a full-page scan. Used ahead of any text statistic because it
    survives the case that defeats them: a scanned page whose only text layer
    is a header stamp.
    """
    try:
        resources = page.get("/Resources")
        if resources is None:
            return 0.0
        xobjects = resources.get_object().get("/XObject")
        if xobjects is None:
            return 0.0
        xobjects = xobjects.get_object()
        width_pt = float(page.mediabox.width) or 1.0
        height_pt = float(page.mediabox.height) or 1.0
        nominal = (width_pt / 72.0 * 150.0) * (height_pt / 72.0 * 150.0)
        if nominal <= 0:
            return 0.0
        total = 0.0
        for key in xobjects:
            obj = xobjects[key].get_object()
            if obj.get("/Subtype") != "/Image":
                continue
            total += float(obj.get("/Width", 0)) * float(obj.get("/Height", 0))
        return total / nominal
    except Exception:  # noqa: BLE001 - a page we cannot inspect is not a scan claim
        return 0.0


def _looks_like_scan(page, native_text: str) -> bool:
    if _image_coverage(page) >= IMAGE_COVERAGE_SCAN_THRESHOLD:
        return True
    stripped = native_text.strip()
    words = [w for w in stripped.split() if w]
    return len(stripped) < MIN_NATIVE_CHARS or len(words) < MIN_NATIVE_WORDS


def _mediabox_is_sane(page) -> bool:
    try:
        box = page.mediabox
        width, height = float(box.width), float(box.height)
    except Exception:  # noqa: BLE001
        return False
    if width <= 0 or height <= 0:
        return False
    return width <= MAX_MEDIABOX_POINTS and height <= MAX_MEDIABOX_POINTS


def _render_pixels(page) -> float:
    box = page.mediabox
    return (float(box.width) / 72.0 * OCR_RENDER_DPI) * (
        float(box.height) / 72.0 * OCR_RENDER_DPI
    )


def _truncate_text(text: str, limit_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return text, False
    # Cut on a character boundary, never mid-codepoint.
    return encoded[:limit_bytes].decode("utf-8", errors="ignore"), True


def _bounded_page_text(
    text: str, limits: ExtractionLimits, total_text_bytes: int
) -> tuple[str, bool, int]:
    """Apply both the per-page and remaining document text budgets.

    The aggregate limit must be part of each truncation, not merely checked at
    the start of a page. Otherwise the page that crosses the limit can exceed
    it by an entire per-page allowance. Failed OCR pages retain native fallback
    text, so they must pass through the same bounds too.
    """
    remaining = max(0, limits.max_total_text_bytes - total_text_bytes)
    bounded, truncated = _truncate_text(
        text,
        min(limits.max_text_bytes_per_page, remaining),
    )
    return bounded, truncated, len(bounded.encode("utf-8"))


def _ocr_page(pdf_bytes: bytes, page_number: int, limits: ExtractionLimits) -> tuple[str, int]:
    """Render one page and OCR it. Returns (text, confidence in basis points).

    One page at a time on purpose: converting a whole document to images before
    OCR would hold every raster in memory simultaneously.
    """
    import pdf2image
    import pytesseract

    images = pdf2image.convert_from_bytes(
        pdf_bytes,
        dpi=OCR_RENDER_DPI,
        first_page=page_number,
        last_page=page_number,
        timeout=limits.ocr_page_timeout_seconds,
    )
    if not images:
        raise ExtractionError("no raster produced")
    image = images[0]
    try:
        data = pytesseract.image_to_data(
            image,
            lang=OCR_LANGUAGES,
            output_type=pytesseract.Output.DICT,
            timeout=limits.ocr_page_timeout_seconds,
        )
    finally:
        image.close()

    words, confidences = [], []
    for token, raw_conf in zip(data.get("text", []), data.get("conf", []), strict=False):
        token = (token or "").strip()
        try:
            conf = float(raw_conf)
        except (TypeError, ValueError):
            continue
        # Tesseract reports -1 for non-text regions; averaging those in would
        # understate confidence on a perfectly good page.
        if not token or conf < 0:
            continue
        words.append(token)
        confidences.append(conf)

    text = " ".join(words)
    if not confidences:
        # No recognised words: an image-only page with nothing legible. Zero
        # confidence is the honest answer, not "no data so assume fine".
        return text, 0
    mean = sum(confidences) / len(confidences)
    return text, int(round(max(0.0, min(100.0, mean)) * 100))


def extract_pages(pdf_bytes: bytes, limits: ExtractionLimits) -> ExtractionResult:
    """Extract every page. Never raises for a single bad page — only when the
    document as a whole cannot be opened."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if not pdf_bytes.startswith(b"%PDF"):
        raise UnreadableDocument("not a PDF")

    try:
        reader = PdfReader(io_bytes(pdf_bytes))
    except (PdfReadError, OSError, ValueError) as exc:
        raise UnreadableDocument(f"unreadable PDF ({_safe_detail(exc)})") from exc

    if reader.is_encrypted:
        # An owner-password-only PDF opens with an empty user password; a real
        # user password does not. decrypt() *returns* a falsy value rather than
        # raising, so the return value is what matters.
        try:
            if not reader.decrypt(""):
                raise UnreadableDocument("password-protected PDF")
        except UnreadableDocument:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UnreadableDocument("password-protected PDF") from exc

    try:
        page_count = len(reader.pages)
    except (PdfReadError, OSError, ValueError) as exc:
        raise UnreadableDocument(f"unreadable PDF ({_safe_detail(exc)})") from exc

    if page_count < 1:
        raise UnreadableDocument("PDF has no pages")
    if page_count > limits.max_pages:
        raise UnreadableDocument(
            f"PDF has {page_count} pages; the limit is {limits.max_pages}"
        )

    ocr_version = _tesseract_version()
    ocr_available = ocr_version != "tesseract-unavailable"

    pages: list[PageExtraction] = []
    total_text_bytes = 0

    for index in range(page_count):
        number = index + 1

        if total_text_bytes >= limits.max_total_text_bytes:
            # Named distinctly so a reviewer can tell "we gave up on the whole
            # document here" from "this page itself was unreadable".
            pages.append(
                PageExtraction(
                    page_number=number,
                    text="",
                    method="failed",
                    failure_code="total_text_budget_exhausted",
                    failure_detail="document text limit reached before this page",
                )
            )
            continue

        try:
            page = reader.pages[index]
        except Exception as exc:  # noqa: BLE001
            pages.append(
                PageExtraction(
                    page_number=number,
                    text="",
                    method="failed",
                    failure_code="page_parse_error",
                    failure_detail=_safe_detail(exc),
                )
            )
            continue

        try:
            native_text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            native_text = ""
            native_error: str | None = _safe_detail(exc)
        else:
            native_error = None

        if not _looks_like_scan(page, native_text):
            text, truncated, text_bytes = _bounded_page_text(
                native_text, limits, total_text_bytes
            )
            total_text_bytes += text_bytes
            pages.append(
                PageExtraction(
                    page_number=number,
                    text=text,
                    method="native",
                    text_truncated=truncated,
                )
            )
            continue

        failure = _page_ocr_precheck(page, ocr_available)
        if failure is not None:
            code, detail = failure
            text, truncated, text_bytes = _bounded_page_text(
                native_text.strip(), limits, total_text_bytes
            )
            total_text_bytes += text_bytes
            pages.append(
                PageExtraction(
                    page_number=number,
                    text=text,
                    method="failed",
                    text_truncated=truncated,
                    failure_code=code,
                    failure_detail=detail,
                )
            )
            continue

        try:
            ocr_text, confidence_bps = _ocr_page(pdf_bytes, number, limits)
        except Exception as exc:  # noqa: BLE001
            code = (
                "ocr_timeout"
                if "timeout" in type(exc).__name__.lower()
                or "timeout" in str(exc)[:80].lower()
                else "raster_timeout"
                if "PDFPopplerTimeoutError" in type(exc).__name__
                else "native_extract_error"
                if native_error
                else "page_parse_error"
            )
            text, truncated, text_bytes = _bounded_page_text(
                native_text.strip(), limits, total_text_bytes
            )
            total_text_bytes += text_bytes
            pages.append(
                PageExtraction(
                    page_number=number,
                    text=text,
                    method="failed",
                    text_truncated=truncated,
                    failure_code=code,
                    failure_detail=_safe_detail(exc),
                )
            )
            continue

        text, truncated, text_bytes = _bounded_page_text(ocr_text, limits, total_text_bytes)
        total_text_bytes += text_bytes
        pages.append(
            PageExtraction(
                page_number=number,
                text=text,
                method="ocr",
                ocr_confidence_bps=confidence_bps,
                text_truncated=truncated,
            )
        )

    return ExtractionResult(
        page_count=page_count,
        pages=tuple(pages),
        parser_version=_parser_version(),
        ocr_engine_version=ocr_version,
        ocr_languages=OCR_LANGUAGES,
        config_snapshot=limits.snapshot(),
    )


def _page_ocr_precheck(page, ocr_available: bool) -> tuple[str, str] | None:
    """Reasons to refuse OCR *before* rendering anything."""
    if not _mediabox_is_sane(page):
        return ("invalid_media_box", "page size is missing or implausibly large")
    if _render_pixels(page) > MAX_RENDER_PIXELS:
        return ("pixel_limit", "page would exceed the rendering pixel limit")
    if not ocr_available:
        return ("ocr_unavailable", "OCR engine is not installed on this server")
    return None


def _parser_version() -> str:
    try:
        import pypdf

        return f"{PARSER_NAME}-{pypdf.__version__}"
    except Exception:  # noqa: BLE001
        return f"{PARSER_NAME}-unknown"


def io_bytes(data: bytes):
    import io

    return io.BytesIO(data)


# ── process isolation ──────────────────────────────────────────────────────


def _child_main(pdf_bytes: bytes, limits: ExtractionLimits, out_path: str, signal_conn) -> None:
    """Runs in the spawned child. Writes its result to a file and sends only a
    short status over the pipe."""
    status = "error"
    try:
        # Become a process-group leader so the parent can kill this process AND
        # any Poppler/Tesseract subprocess it spawns. Without this, a timeout
        # would leave orphaned binaries running.
        os.setsid()

        try:
            import resource

            limit = limits.child_memory_limit_bytes
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            cpu = max(1, limits.overall_timeout_seconds)
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))
        except Exception:  # noqa: BLE001 - platform without rlimits
            pass

        result = extract_pages(pdf_bytes, limits)
        payload = {
            "schema": _RESULT_SCHEMA,
            "pageCount": result.page_count,
            "parserVersion": result.parser_version,
            "ocrEngineVersion": result.ocr_engine_version,
            "ocrLanguages": result.ocr_languages,
            "configSnapshot": result.config_snapshot,
            "pages": [
                {
                    "pageNumber": page.page_number,
                    "text": page.text,
                    "method": page.method,
                    "ocrConfidenceBps": page.ocr_confidence_bps,
                    "textTruncated": page.text_truncated,
                    "failureCode": page.failure_code,
                    "failureDetail": page.failure_detail,
                }
                for page in result.pages
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        # Write-then-rename: the parent must never observe a partial result as
        # if it were complete.
        tmp_path = f"{out_path}.tmp"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, out_path)
        status = "ok"
    except UnreadableDocument as exc:
        status = f"unreadable:{exc}"[:400]
    except MemoryError:
        status = "error:memory limit exceeded"
    except Exception as exc:  # noqa: BLE001
        status = f"error:{_safe_detail(exc)}"
    finally:
        try:
            signal_conn.send(status)
            signal_conn.close()
        except Exception:  # noqa: BLE001
            pass


def _kill_process_group(pid: int) -> None:
    """SIGTERM the group, then SIGKILL. Killing only the Python child would
    leave a hung pdftoppm/tesseract behind holding memory and CPU."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        if sig is signal.SIGTERM:
            import time

            time.sleep(0.5)


def _parse_result_payload(raw: bytes, limits: ExtractionLimits) -> ExtractionResult:
    """Validate strictly. The file is written by our own child, but it is the
    one channel carrying attacker-influenced content back into the parent, so
    it gets schema validation rather than trust (and never pickle)."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtractionError("extraction produced an unreadable result") from exc

    if not isinstance(payload, dict) or payload.get("schema") != _RESULT_SCHEMA:
        raise ExtractionError("extraction result did not match the expected schema")

    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raise ExtractionError("extraction result contained no pages")
    if len(raw_pages) > limits.max_pages:
        raise ExtractionError("extraction result contained too many pages")

    pages: list[PageExtraction] = []
    total_text_bytes = 0
    for index, entry in enumerate(raw_pages, start=1):
        if not isinstance(entry, dict):
            raise ExtractionError("malformed page entry in extraction result")
        method = entry.get("method")
        if method not in {"native", "ocr", "failed"}:
            raise ExtractionError("unknown extraction method in result")
        text = entry.get("text")
        if not isinstance(text, str):
            raise ExtractionError("malformed page text in extraction result")
        text_bytes = len(text.encode("utf-8"))
        if text_bytes > limits.max_text_bytes_per_page:
            raise ExtractionError("page text exceeded the configured limit")
        total_text_bytes += text_bytes
        if total_text_bytes > limits.max_total_text_bytes:
            raise ExtractionError("document text exceeded the configured limit")
        confidence = entry.get("ocrConfidenceBps")
        if confidence is not None and not (
            isinstance(confidence, int) and 0 <= confidence <= 10000
        ):
            raise ExtractionError("malformed OCR confidence in extraction result")
        page_number = entry.get("pageNumber")
        if page_number != index:
            raise ExtractionError("extraction result pages were not in order")
        failure_code = entry.get("failureCode")
        if failure_code is not None and not isinstance(failure_code, str):
            raise ExtractionError("malformed failure code in extraction result")
        detail = entry.get("failureDetail")
        pages.append(
            PageExtraction(
                page_number=page_number,
                text=text,
                method=method,
                ocr_confidence_bps=confidence,
                text_truncated=bool(entry.get("textTruncated")),
                failure_code=failure_code,
                failure_detail=(
                    detail[:MAX_FAILURE_DETAIL_CHARS] if isinstance(detail, str) else None
                ),
            )
        )

    snapshot = payload.get("configSnapshot")
    return ExtractionResult(
        page_count=len(pages),
        pages=tuple(pages),
        parser_version=str(payload.get("parserVersion", "unknown"))[:100],
        ocr_engine_version=str(payload.get("ocrEngineVersion", "unknown"))[:100],
        ocr_languages=str(payload.get("ocrLanguages", OCR_LANGUAGES))[:50],
        config_snapshot=snapshot if isinstance(snapshot, dict) else {},
    )


def run_isolated_extraction(
    pdf_bytes: bytes, limits: ExtractionLimits | None = None
) -> ExtractionResult:
    """Extract in a killable child process. Raises ``UnreadableDocument`` for a
    document that cannot be opened and ``ExtractionError`` for a failure of the
    extraction machinery itself."""
    limits = limits or limits_from_config()

    # spawn, never fork: forking a threaded web server process into libraries
    # that shell out is a well-known source of deadlocks.
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)

    # 0700 so extracted evidence text is not world-readable even briefly.
    tmp_dir = tempfile.mkdtemp(prefix="atlas-argus-ingest-")
    os.chmod(tmp_dir, 0o700)
    out_path = os.path.join(tmp_dir, "result.json")

    process = ctx.Process(
        target=_child_main, args=(pdf_bytes, limits, out_path, child_conn), daemon=True
    )
    try:
        process.start()
        child_conn.close()

        status: str | None = None
        if parent_conn.poll(limits.overall_timeout_seconds):
            try:
                status = parent_conn.recv()
            except EOFError:
                status = None

        process.join(timeout=5)
        if process.is_alive():
            _kill_process_group(process.pid)
            process.join(timeout=5)
            raise ExtractionError(
                f"document processing exceeded {limits.overall_timeout_seconds}s"
            )

        if isinstance(status, str) and status.startswith("unreadable:"):
            raise UnreadableDocument(status.split(":", 1)[1].strip() or "unreadable PDF")
        if status != "ok":
            raise ExtractionError("document processing failed")
        if process.exitcode != 0:
            raise ExtractionError("document processing ended abnormally")

        result_file = Path(out_path)
        if not result_file.exists():
            raise ExtractionError("document processing produced no result")
        # Bound the read: the child is ours, but this is the trust boundary.
        max_result_bytes = limits.max_total_text_bytes + 1024 * 1024
        if result_file.stat().st_size > max_result_bytes:
            raise ExtractionError("extraction result exceeded the configured size")
        return _parse_result_payload(result_file.read_bytes(), limits)
    finally:
        if process.pid and process.is_alive():
            _kill_process_group(process.pid)
            process.join(timeout=5)
        try:
            parent_conn.close()
        except Exception:  # noqa: BLE001
            pass
        # Extracted evidence text must not survive on disk after the request.
        for path in (Path(out_path), Path(f"{out_path}.tmp")):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
