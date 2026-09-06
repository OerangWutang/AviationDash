"""Extraction pipeline: real PDFs, real subprocess isolation.

The fixtures are generated rather than committed so the suite has no binary
blobs, and so each shape is described by the code that builds it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from atlas_argus import ingestion
from atlas_argus.ingestion import (
    ExtractionError,
    ExtractionLimits,
    UnreadableDocument,
    extract_pages,
    normalize_quote_text,
    quote_appears_on_page,
    run_isolated_extraction,
)

pytest.importorskip("pypdf")

NATIVE_BODY = (
    "The flight data recorder shows the stick shaker activated at 2216:27 "
    "with the airspeed decaying through 131 knots. The captain responded "
    "with an aft column input of approximately 37 pounds, opposite the "
    "required recovery input."
)
SECOND_BODY = (
    "Company records indicate the captain had failed three prior checkrides "
    "prior to being hired, none of which were disclosed on the employment "
    "application reviewed by the operator."
)

HAS_TESSERACT = shutil.which("tesseract") is not None
HAS_POPPLER = shutil.which("pdftoppm") is not None
needs_ocr = pytest.mark.skipif(
    not (HAS_TESSERACT and HAS_POPPLER),
    reason="requires tesseract-ocr and poppler-utils",
)


def _limits(**overrides) -> ExtractionLimits:
    base = {
        "max_pages": 200,
        "max_text_bytes_per_page": 50 * 1024,
        "max_total_text_bytes": 24 * 1024 * 1024,
        "ocr_page_timeout_seconds": 20,
        "overall_timeout_seconds": 60,
        "child_memory_limit_bytes": 512 * 1024 * 1024,
    }
    base.update(overrides)
    return ExtractionLimits(**base)


# ── fixture construction ───────────────────────────────────────────────────


def _native_pdf(pages: list[str], *, mediabox: str = "0 0 612 792") -> bytes:
    """A minimal PDF with a genuine text stream (not an image)."""
    streams = []
    for body in pages:
        lines, current = [], ""
        for word in body.split():
            if len(f"{current} {word}") > 78:
                lines.append(current.strip())
                current = word
            else:
                current = f"{current} {word}"
        if current.strip():
            lines.append(current.strip())
        parts = ["BT", "/F1 11 Tf", "1 0 0 1 72 720 Tm", "13 TL"]
        for line in lines:
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            parts.append(f"({escaped}) Tj T*")
        parts.append("ET")
        streams.append("\n".join(parts))

    n = len(streams)
    page_start, content_start = 3, 3 + n
    font_id = content_start + n
    objects = [
        (1, "<< /Type /Catalog /Pages 2 0 R >>"),
        (
            2,
            f"<< /Type /Pages /Kids [{' '.join(f'{page_start + i} 0 R' for i in range(n))}] "
            f"/Count {n} >>",
        ),
    ]
    for i in range(n):
        objects.append(
            (
                page_start + i,
                f"<< /Type /Page /Parent 2 0 R /MediaBox [{mediabox}] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {content_start + i} 0 R >>",
            )
        )
    for i, stream in enumerate(streams):
        objects.append(
            (content_start + i, f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        )
    objects.append((font_id, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num, body in objects:
        offsets[num] = len(out)
        out += f"{num} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref_at = len(out)
    top = max(offsets)
    out += f"xref\n0 {top + 1}\n".encode() + b"0000000000 65535 f \n"
    for num in range(1, top + 1):
        out += f"{offsets.get(num, 0):010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {top + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    )
    return bytes(out)


def _scanned_pdf(body: str, *, noisy: bool = False) -> bytes:
    """A page that exists only as a raster — no text layer at all."""
    from PIL import Image, ImageDraw, ImageFont

    size = (1275, 1650)
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 34)
    except OSError:
        font = ImageFont.load_default()

    words, lines, current = body.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= size[0] - 240:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    y = 140
    for line in lines:
        draw.text((120, y), line, fill="black", font=font)
        y += 48

    if noisy:
        import random

        random.seed(11)
        pixels = image.load()
        for _ in range(int(size[0] * size[1] * 0.03)):
            pixels[random.randrange(size[0]), random.randrange(size[1])] = (110, 110, 110)
        image = image.rotate(1.5, fillcolor="white", resample=Image.BICUBIC)

    import io

    buffer = io.BytesIO()
    image.save(buffer, "PDF", resolution=150.0)
    return buffer.getvalue()


def _scanned_with_native_header() -> bytes:
    """The case that defeats a text-length heuristic: a Bates/protective-order
    header in the text layer over an image-only body."""
    import io

    from pypdf import PdfReader, PdfWriter

    header = PdfReader(
        io.BytesIO(
            _native_pdf(["CONFIDENTIAL - PROTECTIVE ORDER - NTSB-000184 - Page 12 of 340"])
        )
    )
    scan = PdfReader(io.BytesIO(_scanned_pdf(SECOND_BODY)))
    writer = PdfWriter()
    writer.add_page(header.pages[0])
    page = writer.pages[0]
    page.merge_page(scan.pages[0])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# ── normalisation ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("The  quick   brown", "the quick brown"),
        ("“curly quotes”", '"curly quotes"'),
        ("em—dash", "em-dash"),
        ("soft­hyphen", "softhyphen"),
        ("line-\nwrapped", "linewrapped"),
        ("  Leading and trailing  ", "leading and trailing"),
    ],
)
def test_normalisation_folds_typography_not_substance(raw, expected):
    assert normalize_quote_text(raw) == expected


def test_quote_match_survives_pdf_line_wrapping():
    """A reviewer pastes a clean sentence; the PDF has it hyphenated across a
    line break. These are the same quote."""
    page_text = "the airspeed was decay-\ning through 131 knots"
    assert quote_appears_on_page("airspeed was decaying through 131 knots", page_text)


def test_empty_quote_never_matches():
    assert not quote_appears_on_page("", "any page text at all")
    assert not quote_appears_on_page("   ", "any page text at all")


def test_absent_quote_does_not_match():
    assert not quote_appears_on_page("the autopilot was engaged", NATIVE_BODY)


# ── native extraction ──────────────────────────────────────────────────────


def test_native_pages_are_extracted_without_ocr():
    result = extract_pages(_native_pdf([NATIVE_BODY, SECOND_BODY]), _limits())
    assert result.page_count == 2
    assert [p.method for p in result.pages] == ["native", "native"]
    assert result.ocr_page_count == 0
    assert result.failed_page_count == 0
    assert quote_appears_on_page("the stick shaker activated at 2216:27", result.pages[0].text)
    assert quote_appears_on_page("failed three prior checkrides", result.pages[1].text)


def test_page_text_hash_is_stable():
    pdf = _native_pdf([NATIVE_BODY])
    first = extract_pages(pdf, _limits()).pages[0]
    second = extract_pages(pdf, _limits()).pages[0]
    assert first.text_sha256 == second.text_sha256


def test_per_page_truncation_is_recorded_not_hidden():
    """A quote absent from truncated text is unknown, not disproved — so the
    flag has to survive to the verification layer."""
    result = extract_pages(_native_pdf([NATIVE_BODY]), _limits(max_text_bytes_per_page=100))
    page = result.pages[0]
    assert page.text_truncated is True
    assert len(page.text.encode("utf-8")) <= 100


def test_text_within_the_limit_is_not_flagged_truncated():
    result = extract_pages(_native_pdf([NATIVE_BODY]), _limits(max_text_bytes_per_page=50 * 1024))
    assert result.pages[0].text_truncated is False


def test_truncation_never_splits_a_multibyte_character():
    """Cutting a UTF-8 byte string at an arbitrary offset can land mid-
    codepoint. Tested against the helper directly: the minimal PDF builder
    above is latin-1, so it cannot carry the characters that expose this."""
    text = "航空事故調査報告書" * 20  # 3 bytes per character
    for limit in range(10, 60):
        truncated, was_truncated = ingestion._truncate_text(text, limit)
        assert was_truncated is True
        assert len(truncated.encode("utf-8")) <= limit
        # Round-trips cleanly — a mid-codepoint cut would not.
        assert truncated == truncated.encode("utf-8").decode("utf-8")


def test_total_text_budget_marks_remaining_pages_distinctly():
    """Pages skipped for a document-wide budget must not look like pages that
    were individually unreadable."""
    result = extract_pages(
        _native_pdf([NATIVE_BODY] * 5), _limits(max_total_text_bytes=300)
    )
    skipped = [p for p in result.pages if p.failure_code == "total_text_budget_exhausted"]
    assert skipped, "expected later pages to be marked as budget-exhausted"
    assert all(p.method == "failed" for p in skipped)
    assert result.pages[0].method == "native"


# ── malformed and hostile documents ────────────────────────────────────────


def test_non_pdf_is_rejected():
    with pytest.raises(UnreadableDocument, match="not a PDF"):
        extract_pages(b"\x89PNG\r\n\x1a\n" + b"\x00" * 256, _limits())


def test_truncated_pdf_is_rejected():
    with pytest.raises(UnreadableDocument):
        extract_pages(_native_pdf([NATIVE_BODY])[:180], _limits())


def test_password_protected_pdf_is_rejected():
    import io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(_native_pdf([NATIVE_BODY]))).pages:
        writer.add_page(page)
    writer.encrypt("a-real-password")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(UnreadableDocument, match="password"):
        extract_pages(buffer.getvalue(), _limits())


def test_page_count_over_the_limit_is_refused_before_work():
    with pytest.raises(UnreadableDocument, match="limit is 2"):
        extract_pages(_native_pdf([NATIVE_BODY] * 3), _limits(max_pages=2))


def test_absurd_page_canvas_is_refused_without_rendering():
    """A 1 KB file may declare a 200000x200000pt page. At 200 DPI that is a
    multi-terabyte raster, so the size is checked before rendering."""
    pdf = _native_pdf(["x"], mediabox="0 0 200000 200000")
    result = extract_pages(pdf, _limits())
    page = result.pages[0]
    assert page.method == "failed"
    assert page.failure_code in {"invalid_media_box", "pixel_limit"}


def test_failure_detail_never_leaks_paths_or_commands():
    pdf = _native_pdf(["x"], mediabox="0 0 200000 200000")
    for page in extract_pages(pdf, _limits()).pages:
        if page.failure_detail:
            assert "/" not in page.failure_detail
            assert len(page.failure_detail) <= ingestion.MAX_FAILURE_DETAIL_CHARS


# ── the scan/native decision ───────────────────────────────────────────────


def test_scanned_page_with_native_header_is_not_treated_as_native():
    """The spike's counterexample: ~73 characters of header text sit in the
    text layer while the body is a raster. Trusting the length would keep the
    header and silently discard the page body."""
    result = extract_pages(_scanned_with_native_header(), _limits())
    page = result.pages[0]
    assert page.method != "native", (
        "a page whose body is an image must not be classified native on the "
        "strength of a header stamp"
    )


def test_image_only_page_is_not_classified_native():
    result = extract_pages(_scanned_pdf(NATIVE_BODY), _limits())
    assert result.pages[0].method != "native"


@needs_ocr
def test_scanned_page_is_ocred_and_quote_matches():
    result = extract_pages(_scanned_pdf(NATIVE_BODY), _limits())
    page = result.pages[0]
    assert page.method == "ocr"
    assert page.ocr_confidence_bps is not None
    # Assert a stable phrase, never full-page string equality: OCR output
    # drifts between Tesseract releases.
    assert quote_appears_on_page("the stick shaker activated", page.text)


@needs_ocr
def test_degraded_scan_reports_lower_confidence_than_a_clean_one():
    clean = extract_pages(_scanned_pdf(NATIVE_BODY), _limits()).pages[0]
    noisy = extract_pages(_scanned_pdf(NATIVE_BODY, noisy=True), _limits()).pages[0]
    assert clean.ocr_confidence_bps is not None and noisy.ocr_confidence_bps is not None
    assert noisy.ocr_confidence_bps < clean.ocr_confidence_bps


def test_missing_ocr_engine_is_a_named_failure_not_a_silent_empty_page():
    """If Tesseract is absent the page must say so, not return empty text that
    would read as 'this page contains nothing'."""
    result = extract_pages(_scanned_pdf(NATIVE_BODY), _limits())
    page = result.pages[0]
    if page.method == "failed":
        assert page.failure_code is not None
        if not HAS_TESSERACT:
            assert page.failure_code == "ocr_unavailable"


# ── process isolation ──────────────────────────────────────────────────────


def test_isolated_extraction_returns_the_same_result_as_in_process():
    pdf = _native_pdf([NATIVE_BODY, SECOND_BODY])
    isolated = run_isolated_extraction(pdf, _limits())
    inline = extract_pages(pdf, _limits())
    assert [p.text_sha256 for p in isolated.pages] == [p.text_sha256 for p in inline.pages]
    assert isolated.page_count == inline.page_count


def test_isolated_extraction_propagates_unreadable_document():
    with pytest.raises(UnreadableDocument):
        run_isolated_extraction(b"not a pdf at all", _limits())


def test_isolated_extraction_leaves_no_temporary_evidence_behind():
    """Extracted page text is evidence; it must not survive on disk."""
    before = set(Path(tempfile._get_default_tempdir()).glob("atlas-argus-ingest-*"))
    run_isolated_extraction(_native_pdf([NATIVE_BODY]), _limits())
    after = set(Path(tempfile._get_default_tempdir()).glob("atlas-argus-ingest-*"))
    assert after == before


def test_oversized_page_text_is_rejected_by_the_parent():
    """The result file is the one channel carrying attacker-influenced content
    back into the parent, so the parent re-checks sizes rather than trusting
    that the child honoured its own limits."""
    limits = _limits(max_text_bytes_per_page=1024)
    oversized = (
        b'{"schema":"atlas_argus.extraction_result.v1","pages":[{"pageNumber":1,'
        b'"text":"' + b"x" * 4096 + b'","method":"native"}]}'
    )
    with pytest.raises(ExtractionError, match="exceeded the configured limit"):
        ingestion._parse_result_payload(oversized, limits)


@pytest.mark.parametrize(
    "payload",
    [
        b"{ not json",
        b'{"schema":"wrong.schema","pages":[]}',
        b'{"schema":"atlas_argus.extraction_result.v1","pages":[]}',
        b'{"schema":"atlas_argus.extraction_result.v1","pages":[{"pageNumber":1,'
        b'"text":"t","method":"telepathy"}]}',
        b'{"schema":"atlas_argus.extraction_result.v1","pages":[{"pageNumber":7,'
        b'"text":"t","method":"native"}]}',
        b'{"schema":"atlas_argus.extraction_result.v1","pages":[{"pageNumber":1,'
        b'"text":"t","method":"ocr","ocrConfidenceBps":99999}]}',
    ],
)
def test_malformed_child_results_are_rejected(payload):
    with pytest.raises(ExtractionError):
        ingestion._parse_result_payload(payload, _limits())


def _process_is_alive(pid: int) -> bool:
    """True only if the process is actually running.

    `os.kill(pid, 0)` is not enough: it succeeds for a zombie, whose PID stays
    in the table until someone reaps it. That distinction is invisible on a
    host with a reaping init but decides this test inside a container, where
    PID 1 is the application and orphans linger unreaped.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return False
    except OSError:
        # No procfs (non-Linux): fall back to the weaker signal probe.
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
    # Fields after the ")" of comm; the first is the state character.
    return stat.rsplit(") ", 1)[1].split()[0] not in {"Z", "X", "x"}


def test_timeout_kills_the_whole_process_group(tmp_path):
    """A hung Poppler/Tesseract is not killed by terminating the Python child
    that spawned it. Simulate the shape: a child whose grandchild sleeps."""
    script = tmp_path / "hang.py"
    marker = tmp_path / "grandchild.pid"
    script.write_text(
        "import os, subprocess, sys, time\n"
        "os.setsid()\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(120)\n"
    )
    process = subprocess.Popen([sys.executable, str(script)])
    try:
        for _ in range(100):
            if marker.exists():
                break
            time.sleep(0.05)
        grandchild = int(marker.read_text())
        ingestion._kill_process_group(process.pid)
        process.wait(timeout=10)

        deadline = time.time() + 5
        while time.time() < deadline and _process_is_alive(grandchild):
            time.sleep(0.05)
        assert not _process_is_alive(grandchild), (
            "grandchild survived the group kill — an orphaned binary would leak"
        )
    finally:
        if process.poll() is None:
            process.kill()
