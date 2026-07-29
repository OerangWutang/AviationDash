"""Paginated PDF rendering for evidence packets.

Browser print-to-PDF was never a controlled artifact: pagination, margins,
headers and page numbering all depend on whoever pressed Ctrl-P and which
browser they used, so two people producing "the same" packet could hand over
documents that differ in what appears on which page. For something served on
opposing counsel or filed with a court, that is the whole point of the
document.

This renders the packet server-side, so the produced PDF is deterministic
given the same inputs, carries page numbers and per-page provenance, and is
stored and hashed exactly as it was served.

Two design points worth stating plainly.

**The stored bytes are authoritative, not the ability to re-render.** The PDF
is hashed at generation and that hash joins the packet's integrity chain.
Verification recomputes the hash of the *stored* bytes; it never re-renders,
because output legitimately depends on the installed WeasyPrint, fontTools,
Pango and font versions, and an image rebuild may change it. Reproducibility
below is a hygiene property that makes tests meaningful — not the thing the
evidentiary claim rests on.

**Fonts are a deployment dependency, not a detail.** The stylesheet asks for
serif and sans families by name. On an image without them the text silently
falls back and paginates differently, which is exactly the kind of failure a
byte-count assertion would not catch. The image installs metric-compatible
families; see the Dockerfile.
"""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from datetime import datetime

from .packet import EvidencePacket

#: Public docket numbers vary in length; Letter is the US litigation default.
PAGE_SIZE = "Letter"

#: Everything the screen stylesheet cannot express: real pages.
#:
#: The running footer repeats the packet id and the body hash on *every* page
#: so a single detached page can still be tied back to the packet it came
#: from — a page that falls out of a binder is a normal occurrence in
#: litigation, and an unidentifiable one is worthless.
PACKET_PRINT_CSS = """
@page {
  size: %(size)s;
  margin: 20mm 18mm 22mm 18mm;

  @top-center {
    content: "%(stamp)s";
    font: bold 8pt Arial, "Liberation Sans", sans-serif;
    letter-spacing: 0.08em;
    color: %(stamp_color)s;
    padding-bottom: 3mm;
  }

  @bottom-left {
    content: "%(packet_id)s";
    font: 7.5pt ui-monospace, "DejaVu Sans Mono", monospace;
    color: #5c626e;
  }

  @bottom-center {
    content: "Page " counter(page) " of " counter(pages);
    font: 8pt Arial, "Liberation Sans", sans-serif;
    color: #3c414b;
  }

  @bottom-right {
    content: "%(short_hash)s";
    font: 7.5pt ui-monospace, "DejaVu Sans Mono", monospace;
    color: #5c626e;
  }
}

body { background: #fff; margin: 0; }

/* The screen layout centres a fixed-width card on a grey field. On paper the
   page box already provides the margins, so the card would double them. */
.packet {
  max-width: none;
  margin: 0;
  padding: 0;
  background: #fff;
  color: #16181d;
  font: 10.5pt/1.5 Georgia, "Liberation Serif", "DejaVu Serif", serif;
}

h1 { font-size: 18pt; margin: 0 0 10pt; }
h2 { font-size: 13pt; margin: 18pt 0 8pt; break-after: avoid; }
h3 { font-size: 11.5pt; margin: 12pt 0 5pt; break-after: avoid; }
h4 { font-size: 10.5pt; margin: 10pt 0 4pt; break-after: avoid; }

/* A heading stranded at the foot of a page, or a claim split across a page
   break so its quote lands away from the claim it supports, makes the
   document harder to rely on in a deposition. */
.appendix-claim,
.appendix-source,
.decision,
.disclosure,
.disclosure-claim,
blockquote { break-inside: avoid; }

.entry { break-inside: auto; }

table { break-inside: auto; }
.manifest tr { break-inside: avoid; }
.manifest thead { display: table-header-group; }

/* Repeating the caption block on page one only; the running header carries
   the classification from then on. */
.stamp { break-after: avoid; }

blockquote {
  margin: 5pt 0 8pt;
  padding: 4pt 10pt;
  border-left: 2pt solid #c9cdd6;
  font-style: italic;
  color: #3c414b;
  font-size: 9.5pt;
}

.integrity {
  font: 7.5pt ui-monospace, "DejaVu Sans Mono", monospace;
  color: #5c626e;
  word-break: break-all;
}

/* On screen the footer closes the card. In print the running footer carries
   the per-page provenance, so this block is the full-detail version and
   should start its own page rather than orphaning two lines. */
footer { margin-top: 18pt; border-top: 0.5pt solid #c9cdd6; padding-top: 6pt; }

a { color: inherit; text-decoration: none; }
"""

_STAMP_TEXT = {
    "internal": "INTERNAL WORK PRODUCT — NOT FOR PRODUCTION",
    "production": "PRODUCTION COPY",
}
_STAMP_COLOR = {"internal": "#8a1f1a", "production": "#1d4f8a"}


class PdfRenderingUnavailable(RuntimeError):
    """WeasyPrint or its system libraries are not installed."""


class PdfTooLarge(RuntimeError):
    """The rendered document exceeded the configured ceiling."""


def print_stylesheet(packet_type: str, packet_id: str, body_sha256: str) -> str:
    return PACKET_PRINT_CSS % {
        "size": PAGE_SIZE,
        "stamp": _STAMP_TEXT.get(packet_type, packet_type.upper()),
        "stamp_color": _STAMP_COLOR.get(packet_type, "#3c414b"),
        "packet_id": packet_id,
        # Enough to identify, short enough to sit in a footer margin. The full
        # hash is in the document body and in the artifact record.
        "short_hash": f"sha256:{body_sha256[:16]}…",
    }


@contextmanager
def _pinned_build_clock(moment: datetime):
    """Pin the clock fontTools stamps into embedded font subsets.

    Without this, two renders of identical input differ: the TrueType ``head``
    table carries a modification time, which changes its checksum, which
    changes the compressed stream and shifts every later cross-reference
    offset. The reproducible-builds convention is ``SOURCE_DATE_EPOCH``.

    Pinned to the packet's own generation time rather than a constant, so the
    document's internal dates agree with the packet and the rendering
    machine's wall clock never leaks into font metadata.
    """
    previous = os.environ.get("SOURCE_DATE_EPOCH")
    os.environ["SOURCE_DATE_EPOCH"] = str(int(moment.timestamp()))
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("SOURCE_DATE_EPOCH", None)
        else:
            os.environ["SOURCE_DATE_EPOCH"] = previous


def pdf_available() -> bool:
    try:
        import weasyprint  # noqa: F401
    except Exception:  # noqa: BLE001 - missing system libs raise more than ImportError
        return False
    return True


def _inject_print_css(document_html: str, print_css: str) -> str:
    """Add the print rules to the stored HTML document.

    The PDF is rendered from the *same* HTML that is stored and served, rather
    than a separately assembled document, so the two cannot drift into saying
    different things. Only pagination rules are added on top.
    """
    style_block = f"\n<style>{print_css}</style>\n</head>"
    if "</head>" in document_html:
        return document_html.replace("</head>", style_block, 1)
    # Defensive: a document without a head is not something we generate, but
    # silently producing an unstyled PDF would be worse than being obvious.
    return f"<style>{print_css}</style>\n{document_html}"


def render_packet_pdf(
    packet: EvidencePacket,
    document_html: str,
    body_sha256: str,
    *,
    generated_at: datetime,
    max_bytes: int,
) -> bytes:
    """Render the stored HTML document to a paginated PDF."""
    try:
        from weasyprint import HTML
    except Exception as exc:  # noqa: BLE001
        raise PdfRenderingUnavailable(
            "PDF rendering requires WeasyPrint and its system libraries."
        ) from exc

    html = _inject_print_css(
        document_html, print_stylesheet(packet.type, packet.packet_id, body_sha256)
    )

    with _pinned_build_clock(generated_at):
        # base_url=None: the document is self-contained by construction, and
        # refusing a base URL means a stray external reference cannot make the
        # renderer fetch anything while producing evidence.
        pdf = HTML(string=html, base_url=None).write_pdf()

    if len(pdf) > max_bytes:
        raise PdfTooLarge(
            f"Rendered packet PDF is {len(pdf)} bytes; the limit is {max_bytes}."
        )
    return pdf


def pdf_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pdf_filename(packet: EvidencePacket) -> str:
    """Mirrors ``packet_filename`` but with the .pdf extension, so the two
    artifacts of one packet sit together when downloaded."""
    timestamp = packet.generated_at.replace("-", "").replace(":", "")[:13]
    return f"atlas-argus-{packet.type}-packet-{packet.case.id}-{timestamp}.pdf"


_UNSAFE_HEADER = re.compile(r'[^A-Za-z0-9._-]')


def content_disposition_filename(filename: str) -> str:
    """A filename safe to place in a Content-Disposition header.

    Header injection through a generated filename is not a live risk here —
    every component is server-derived — but the header is a place where
    reflected values have historically gone wrong, so it is constrained rather
    than trusted.
    """
    return _UNSAFE_HEADER.sub("-", filename) or "packet.pdf"


__all__ = [
    "PACKET_PRINT_CSS",
    "PdfRenderingUnavailable",
    "PdfTooLarge",
    "content_disposition_filename",
    "pdf_available",
    "pdf_filename",
    "pdf_sha256",
    "print_stylesheet",
    "render_packet_pdf",
]
