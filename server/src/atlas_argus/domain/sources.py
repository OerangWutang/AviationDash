"""Validation and derived facts for uploaded source documents.

Kept out of ``services.py`` so the rules are testable without a database, and
so the two places that need them — the pre-extraction phase and the persistence
phase of an upload — cannot drift apart.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import unicodedata
from datetime import datetime

from ..integrity import canonical_sha256, iso_z
from . import types as t

MAX_TITLE_CHARS = 300
MAX_ORIGIN_CHARS = 200
MAX_CUSTODIAN_CHARS = 200
MAX_DOCKET_REF_CHARS = 120
MAX_FILENAME_CHARS = 255
MAX_IDEMPOTENCY_KEY_CHARS = 100

PDF_MIME_TYPE = "application/pdf"

#: Control characters and path separators have no place in a filename that will
#: be rendered in a browser, written to an audit record, and shown in a packet.
_UNSAFE_FILENAME_CHARS = re.compile(r"[\x00-\x1f\x7f<>:\"/\\|?*]")
_DOT_SEGMENTS = re.compile(r"\.{2,}")


def sanitize_filename(raw: str) -> str:
    """Reduce a client-supplied filename to something safe to store and show.

    This is display and provenance metadata, never used to open a path — but it
    ends up in custody records and packet output, so traversal sequences and
    control characters are stripped rather than trusted.
    """
    text = unicodedata.normalize("NFKC", raw or "").strip()
    # Take the basename regardless of separator flavour before scrubbing.
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    text = _UNSAFE_FILENAME_CHARS.sub("", text)
    text = _DOT_SEGMENTS.sub(".", text).strip(". ")
    text = text[:MAX_FILENAME_CHARS].strip()
    return text or "document.pdf"


def _require_text(value: str | None, *, field: str, maximum: int) -> str:
    text = (value or "").strip()
    if not text:
        raise t.ValidationFailure(f"{field} is required.")
    if len(text) > maximum:
        raise t.ValidationFailure(f"{field} must be {maximum} characters or fewer.")
    return text


def validate_source_metadata(
    *,
    title: str | None,
    source_type: str | None,
    origin: str | None,
    custodian: str | None,
    docket_ref: str | None,
    privilege_status: str | None,
) -> dict[str, str | None]:
    if source_type not in t.SOURCE_TYPES:
        raise t.ValidationFailure("Unknown source type.")
    if privilege_status not in t.PRIVILEGE_STATUSES:
        raise t.ValidationFailure("Unknown privilege status.")
    docket = (docket_ref or "").strip() or None
    if docket is not None and len(docket) > MAX_DOCKET_REF_CHARS:
        raise t.ValidationFailure(
            f"Docket reference must be {MAX_DOCKET_REF_CHARS} characters or fewer."
        )
    return {
        "title": _require_text(title, field="Title", maximum=MAX_TITLE_CHARS),
        "type": source_type,
        "origin": _require_text(origin, field="Origin", maximum=MAX_ORIGIN_CHARS),
        "custodian": _require_text(custodian, field="Custodian", maximum=MAX_CUSTODIAN_CHARS),
        "docketRef": docket,
        "privilegeStatus": privilege_status,
    }


def decode_upload(content_base64: str, *, max_bytes: int) -> bytes:
    """Decode and size-check an uploaded document.

    The encoded length is checked *before* decoding: base64 expands 3 bytes to
    4, so a caller that slips past the request-body limit must not be able to
    make the server materialise an oversized buffer just to measure it.
    """
    if not content_base64:
        raise t.ValidationFailure("A document file is required.")
    # Ceiling of the largest encoding of max_bytes, plus padding slack.
    max_encoded = -(-max_bytes * 4 // 3) + 8
    if len(content_base64) > max_encoded:
        raise t.ValidationFailure("Document exceeds the maximum upload size.")
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise t.ValidationFailure("Document content is not valid base64.") from exc
    if not raw:
        raise t.ValidationFailure("Document is empty.")
    if len(raw) > max_bytes:
        raise t.ValidationFailure("Document exceeds the maximum upload size.")
    if not raw.startswith(b"%PDF"):
        raise t.ValidationFailure("Only PDF documents can be uploaded.")
    return raw


def content_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def validate_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    key = value.strip()
    if not key:
        return None
    if len(key) > MAX_IDEMPOTENCY_KEY_CHARS:
        raise t.ValidationFailure("Idempotency key is too long.")
    return key


def idempotency_fingerprint(
    *,
    case_id: str,
    content_hash: str,
    metadata: dict[str, str | None],
    original_filename: str,
) -> str:
    """Identify *the same upload*, so a retry returns the first result and a
    reused key with different content is caught instead of silently answering
    with an unrelated document."""
    return canonical_sha256(
        {
            "schema": "atlas_argus.source_upload_fingerprint.v1",
            "caseId": case_id,
            "contentSha256": content_hash,
            "title": metadata["title"],
            "type": metadata["type"],
            "origin": metadata["origin"],
            "custodian": metadata["custodian"],
            "docketRef": metadata["docketRef"],
            "privilegeStatus": metadata["privilegeStatus"],
            "originalFilename": original_filename,
        }
    )


def build_custody_record(
    *,
    at: datetime,
    reviewer_name: str,
    matter_role: str,
    original_filename: str,
    content_hash: str,
) -> list[dict[str, str]]:
    """The first custody entry, built entirely from what the server knows.

    Deliberately not accepted from the client: custody is the chain that makes
    a document admissible, so a request body must not be able to assert who
    handled it or when.
    """
    return [
        {
            "at": iso_z(at),
            "actor": reviewer_name,
            "action": (
                f"Uploaded to Atlas Argus as {matter_role} "
                f"(file {original_filename}, SHA-256 {content_hash[:16]}…, "
                f"ingestion channel: web upload)"
            ),
        }
    ]


def default_page_ref(page_number: int) -> str:
    """Pre-fill only. ``page_ref`` is the legal citation locator and stays
    reviewer-editable: an exhibit's own numbering routinely differs from the
    PDF's page index."""
    return f"PDF page {page_number}"
