"""Deployment configuration and fail-closed production guards.

``ATLAS_ARGUS_ENV=production`` switches the guards on: the process refuses to
start with dev defaults (database credentials, public metrics, insecure
cookies) rather than running quietly misconfigured. Failing closed at boot is
cheaper than discovering a default credential in an incident review.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_DB_CREDENTIALS = "atlas:atlas@"
DEFAULT_DATABASE_URL = "postgresql+psycopg://atlas:atlas@localhost:5434/atlas_argus"
DEFAULT_SEED_PASSWORD = "argus-demo"
DEFAULT_MAX_REQUEST_BODY_BYTES = 256 * 1024
DEFAULT_MAX_PACKET_ENTRIES = 250
DEFAULT_MAX_PACKET_DOCUMENT_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_PACKET_MANIFEST_BYTES = 512 * 1024
DEFAULT_MAX_PACKET_ARTIFACT_BYTES = 3 * 1024 * 1024
MAX_CONFIGURED_BYTES = 1024 * 1024 * 1024
MAX_CONFIGURED_PACKET_ENTRIES = 1_000_000

# ── Document ingestion ─────────────────────────────────────────────────────
# Ingestion holds an entire PDF, a rendered page raster, and OCR working
# memory in one process, so these ceilings are much tighter than the generic
# byte limits above: a 1 GiB upload cap would be a memory-exhaustion switch,
# not a configuration option.
DEFAULT_MAX_SOURCE_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_CONFIGURED_SOURCE_UPLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_SOURCE_PAGE_COUNT = 200
DEFAULT_SOURCE_OCR_PAGE_TIMEOUT_SECONDS = 20
DEFAULT_SOURCE_INGESTION_TIMEOUT_SECONDS = 300
DEFAULT_MAX_EXTRACTED_TEXT_BYTES_PER_PAGE = 50 * 1024
#: Per-page caps alone still permit 500 pages x 1 MiB in memory at once; this
#: is the bound that actually holds.
DEFAULT_MAX_TOTAL_EXTRACTED_TEXT_BYTES = 24 * 1024 * 1024
DEFAULT_MAX_CONCURRENT_SOURCE_INGESTIONS = 2
DEFAULT_SOURCE_CHILD_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
DEFAULT_MIN_OCR_CONFIDENCE_FOR_AUTO_VERIFY = 70
#: A paginated PDF is several times the size of its HTML source (embedded font
#: subsets alone are tens of kilobytes), so this is its own ceiling rather than
#: a share of the artifact limit.
DEFAULT_MAX_PACKET_PDF_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_CASE_STATE_ROWS = 10_000
DEFAULT_MAX_PACKET_HISTORY_ROWS = 1_000
DEFAULT_MAX_CONCURRENT_PACKET_RENDERS = 1
DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS = 120
DEFAULT_API_MEMORY_LIMIT_BYTES = 1536 * 1024 * 1024
API_BASELINE_MEMORY_RESERVE_BYTES = 256 * 1024 * 1024
#: Headroom for the JSON envelope and metadata fields around the base64 blob.
SOURCE_REQUEST_METADATA_OVERHEAD_BYTES = 16 * 1024
#: A child must fit the decoded PDF, a rendered page raster, and OCR working
#: memory. Below this multiple of the upload cap, every real upload OOMs.
SOURCE_CHILD_MEMORY_MULTIPLE = 3

_BOUNDED_LIMITS = {
    "ATLAS_ARGUS_MAX_REQUEST_BODY_BYTES": (1024, MAX_CONFIGURED_BYTES),
    "ATLAS_ARGUS_MAX_PACKET_ENTRIES": (1, MAX_CONFIGURED_PACKET_ENTRIES),
    "ATLAS_ARGUS_MAX_PACKET_DOCUMENT_BYTES": (4096, MAX_CONFIGURED_BYTES),
    "ATLAS_ARGUS_MAX_PACKET_MANIFEST_BYTES": (4096, MAX_CONFIGURED_BYTES),
    "ATLAS_ARGUS_MAX_PACKET_ARTIFACT_BYTES": (4096, MAX_CONFIGURED_BYTES),
    "ATLAS_ARGUS_MAX_SOURCE_UPLOAD_BYTES": (
        1024 * 1024,
        MAX_CONFIGURED_SOURCE_UPLOAD_BYTES,
    ),
    "ATLAS_ARGUS_MAX_SOURCE_PAGE_COUNT": (1, 500),
    "ATLAS_ARGUS_SOURCE_OCR_PAGE_TIMEOUT_SECONDS": (1, 120),
    "ATLAS_ARGUS_SOURCE_INGESTION_TIMEOUT_SECONDS": (10, 900),
    "ATLAS_ARGUS_MAX_EXTRACTED_TEXT_BYTES_PER_PAGE": (4096, 1024 * 1024),
    "ATLAS_ARGUS_MAX_TOTAL_EXTRACTED_TEXT_BYTES": (256 * 1024, 64 * 1024 * 1024),
    "ATLAS_ARGUS_MAX_CONCURRENT_SOURCE_INGESTIONS": (1, 8),
    "ATLAS_ARGUS_SOURCE_CHILD_MEMORY_LIMIT_BYTES": (
        64 * 1024 * 1024,
        4 * 1024 * 1024 * 1024,
    ),
    "ATLAS_ARGUS_MIN_OCR_CONFIDENCE_FOR_AUTO_VERIFY": (0, 100),
    "ATLAS_ARGUS_MAX_PACKET_PDF_BYTES": (64 * 1024, 128 * 1024 * 1024),
    "ATLAS_ARGUS_MAX_CASE_STATE_ROWS": (100, 100_000),
    "ATLAS_ARGUS_MAX_PACKET_HISTORY_ROWS": (100, 10_000),
    "ATLAS_ARGUS_MAX_CONCURRENT_PACKET_RENDERS": (1, 4),
    "ATLAS_ARGUS_REQUEST_BODY_TIMEOUT_SECONDS": (5, 900),
    "ATLAS_ARGUS_API_MEMORY_LIMIT_BYTES": (
        512 * 1024 * 1024,
        16 * 1024 * 1024 * 1024,
    ),
}


def env_name() -> str:
    return os.environ.get("ATLAS_ARGUS_ENV", "dev")


def is_production() -> bool:
    return env_name() == "production"


def cookie_secure() -> bool:
    """Secure cookies: explicit opt-in in dev, always on in production."""
    return is_production() or os.environ.get("ATLAS_ARGUS_COOKIE_SECURE", "") == "1"


def seed_password() -> str:
    return os.environ.get("ATLAS_ARGUS_SEED_PASSWORD", "") or DEFAULT_SEED_PASSWORD


def runtime_database_url() -> str:
    return os.environ.get("ATLAS_ARGUS_DATABASE_URL", "") or DEFAULT_DATABASE_URL


def migration_database_url() -> str:
    return os.environ.get("ATLAS_ARGUS_MIGRATION_DATABASE_URL", "") or runtime_database_url()


def metrics_token() -> str:
    return os.environ.get("ATLAS_ARGUS_METRICS_TOKEN", "")


def integrity_anchor_dir() -> Path | None:
    raw = os.environ.get("ATLAS_ARGUS_INTEGRITY_ANCHOR_DIR", "").strip()
    return Path(raw) if raw else None


def integrity_anchor_key_file() -> Path | None:
    raw = os.environ.get("ATLAS_ARGUS_INTEGRITY_ANCHOR_KEY_FILE", "").strip()
    return Path(raw) if raw else None


def max_request_body_bytes() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_MAX_REQUEST_BODY_BYTES",
        DEFAULT_MAX_REQUEST_BODY_BYTES,
        minimum=1024,
        maximum=MAX_CONFIGURED_BYTES,
    )


def max_packet_entries() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_MAX_PACKET_ENTRIES",
        DEFAULT_MAX_PACKET_ENTRIES,
        minimum=1,
        maximum=MAX_CONFIGURED_PACKET_ENTRIES,
    )


def max_packet_document_bytes() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_MAX_PACKET_DOCUMENT_BYTES",
        DEFAULT_MAX_PACKET_DOCUMENT_BYTES,
        minimum=4096,
        maximum=MAX_CONFIGURED_BYTES,
    )


def max_packet_manifest_bytes() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_MAX_PACKET_MANIFEST_BYTES",
        DEFAULT_MAX_PACKET_MANIFEST_BYTES,
        minimum=4096,
        maximum=MAX_CONFIGURED_BYTES,
    )


def max_packet_artifact_bytes() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_MAX_PACKET_ARTIFACT_BYTES",
        DEFAULT_MAX_PACKET_ARTIFACT_BYTES,
        minimum=4096,
        maximum=MAX_CONFIGURED_BYTES,
    )


def max_packet_pdf_bytes() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_MAX_PACKET_PDF_BYTES",
        DEFAULT_MAX_PACKET_PDF_BYTES,
        minimum=64 * 1024,
        maximum=128 * 1024 * 1024,
    )


def max_case_state_rows() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_MAX_CASE_STATE_ROWS",
        DEFAULT_MAX_CASE_STATE_ROWS,
        minimum=100,
        maximum=100_000,
    )


def max_packet_history_rows() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_MAX_PACKET_HISTORY_ROWS",
        DEFAULT_MAX_PACKET_HISTORY_ROWS,
        minimum=100,
        maximum=10_000,
    )


def max_concurrent_packet_renders() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_MAX_CONCURRENT_PACKET_RENDERS",
        DEFAULT_MAX_CONCURRENT_PACKET_RENDERS,
        minimum=1,
        maximum=4,
    )


def max_source_upload_bytes() -> int:
    """Decoded PDF size. The HTTP body is larger — see
    ``max_source_request_bytes``."""
    return _bounded_int(
        "ATLAS_ARGUS_MAX_SOURCE_UPLOAD_BYTES",
        DEFAULT_MAX_SOURCE_UPLOAD_BYTES,
        minimum=1024 * 1024,
        maximum=MAX_CONFIGURED_SOURCE_UPLOAD_BYTES,
    )


def max_source_request_bytes() -> int:
    """Request-body ceiling for the upload route.

    Derived, not separately configured: the body carries the PDF base64-encoded
    (4 bytes out per 3 in) plus a JSON envelope. A hand-set second limit would
    drift from the first and reject files that are legally within the
    configured size.
    """
    raw = max_source_upload_bytes()
    return -(-raw * 4 // 3) + SOURCE_REQUEST_METADATA_OVERHEAD_BYTES


def max_source_page_count() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_MAX_SOURCE_PAGE_COUNT",
        DEFAULT_MAX_SOURCE_PAGE_COUNT,
        minimum=1,
        maximum=500,
    )


def source_ocr_page_timeout_seconds() -> int:
    """Per-page ceiling for a pathological page, not the expected cost."""
    return _bounded_int(
        "ATLAS_ARGUS_SOURCE_OCR_PAGE_TIMEOUT_SECONDS",
        DEFAULT_SOURCE_OCR_PAGE_TIMEOUT_SECONDS,
        minimum=1,
        maximum=120,
    )


def source_ingestion_timeout_seconds() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_SOURCE_INGESTION_TIMEOUT_SECONDS",
        DEFAULT_SOURCE_INGESTION_TIMEOUT_SECONDS,
        minimum=10,
        maximum=900,
    )


def request_body_timeout_seconds() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_REQUEST_BODY_TIMEOUT_SECONDS",
        DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
        minimum=5,
        maximum=900,
    )


def api_memory_limit_bytes() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_API_MEMORY_LIMIT_BYTES",
        DEFAULT_API_MEMORY_LIMIT_BYTES,
        minimum=512 * 1024 * 1024,
        maximum=16 * 1024 * 1024 * 1024,
    )


def max_extracted_text_bytes_per_page() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_MAX_EXTRACTED_TEXT_BYTES_PER_PAGE",
        DEFAULT_MAX_EXTRACTED_TEXT_BYTES_PER_PAGE,
        minimum=4096,
        maximum=1024 * 1024,
    )


def max_total_extracted_text_bytes() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_MAX_TOTAL_EXTRACTED_TEXT_BYTES",
        DEFAULT_MAX_TOTAL_EXTRACTED_TEXT_BYTES,
        minimum=256 * 1024,
        maximum=64 * 1024 * 1024,
    )


def max_concurrent_source_ingestions() -> int:
    """Per-API-process. Deployment pins one worker precisely so this is also
    the deployment-wide limit — see ``docs/production-readiness.md``."""
    return _bounded_int(
        "ATLAS_ARGUS_MAX_CONCURRENT_SOURCE_INGESTIONS",
        DEFAULT_MAX_CONCURRENT_SOURCE_INGESTIONS,
        minimum=1,
        maximum=8,
    )


def source_child_memory_limit_bytes() -> int:
    return _bounded_int(
        "ATLAS_ARGUS_SOURCE_CHILD_MEMORY_LIMIT_BYTES",
        DEFAULT_SOURCE_CHILD_MEMORY_LIMIT_BYTES,
        minimum=64 * 1024 * 1024,
        maximum=4 * 1024 * 1024 * 1024,
    )


def min_ocr_confidence_for_auto_verify() -> int:
    """Percent. A quote found in text this uncertain is routed to human
    attestation rather than badged as verified."""
    return _bounded_int(
        "ATLAS_ARGUS_MIN_OCR_CONFIDENCE_FOR_AUTO_VERIFY",
        DEFAULT_MIN_OCR_CONFIDENCE_FOR_AUTO_VERIFY,
        minimum=0,
        maximum=100,
    )


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        if is_production():
            raise RuntimeError(
                f"{name} must be an integer between {minimum} and {maximum}."
            ) from None
        return default
    if value < minimum or value > maximum:
        if is_production():
            raise RuntimeError(
                f"{name} must be between {minimum} and {maximum}."
            )
        return min(maximum, max(minimum, value))
    return value


def _configured_limit_error(name: str, minimum: int, maximum: int) -> str | None:
    raw = os.environ.get(name, "")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return f"{name} must be an integer between {minimum} and {maximum}."
    if value < minimum or value > maximum:
        return f"{name} must be between {minimum} and {maximum}."
    return None


def _production_cors_errors() -> list[str]:
    raw = os.environ.get("ATLAS_ARGUS_CORS_ORIGINS", "")
    errors: list[str] = []
    if not raw:
        return errors
    for configured in raw.split(","):
        origin = configured.strip()
        if not origin:
            errors.append("ATLAS_ARGUS_CORS_ORIGINS must not contain empty origins.")
            continue
        if origin in {"*", "null"}:
            errors.append(
                "ATLAS_ARGUS_CORS_ORIGINS must not contain wildcard or null origins."
            )
            continue
        try:
            parsed = urlsplit(origin)
            port = parsed.port
        except ValueError:
            parsed = None
            port = None
        if (
            parsed is None
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != ""
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            errors.append(
                "ATLAS_ARGUS_CORS_ORIGINS entries must be exact HTTPS origins "
                f"without credentials, paths, queries, or fragments: {origin}"
            )
    return errors


def production_value_config_errors(*, require_migration_url: bool = False) -> list[str]:
    """Validate production values without probing optional runtime libraries."""
    if not is_production():
        return []
    errors: list[str] = []
    database_url = runtime_database_url()
    if not database_url or DEFAULT_DB_CREDENTIALS in database_url:
        errors.append(
            "ATLAS_ARGUS_DATABASE_URL must be set and must not use the default "
            "atlas:atlas credentials."
        )
    migration_url = os.environ.get("ATLAS_ARGUS_MIGRATION_DATABASE_URL", "")
    if require_migration_url:
        if not migration_url:
            errors.append(
                "ATLAS_ARGUS_MIGRATION_DATABASE_URL must be set for production "
                "schema migrations."
            )
        elif DEFAULT_DB_CREDENTIALS in migration_url:
            errors.append(
                "ATLAS_ARGUS_MIGRATION_DATABASE_URL must not use the default "
                "atlas:atlas credentials."
            )
    if migration_url and migration_url == database_url:
        errors.append(
            "ATLAS_ARGUS_MIGRATION_DATABASE_URL must be distinct from "
            "ATLAS_ARGUS_DATABASE_URL so the API does not run as the schema owner."
        )
    if not metrics_token():
        errors.append(
            "ATLAS_ARGUS_METRICS_TOKEN must be set so /api/metrics is not "
            "publicly exposed in production."
        )
    errors.extend(_production_cors_errors())
    for name, (minimum, maximum) in _BOUNDED_LIMITS.items():
        if error := _configured_limit_error(name, minimum, maximum):
            errors.append(error)
    errors.extend(_ingestion_budget_errors())
    errors.extend(_integrity_anchor_errors())
    return errors


def _integrity_anchor_errors() -> list[str]:
    directory = integrity_anchor_dir()
    key_file = integrity_anchor_key_file()
    errors: list[str] = []
    if directory is None:
        errors.append("ATLAS_ARGUS_INTEGRITY_ANCHOR_DIR is required in production.")
    elif not directory.is_dir():
        errors.append("ATLAS_ARGUS_INTEGRITY_ANCHOR_DIR must be a readable directory.")
    if key_file is None:
        errors.append("ATLAS_ARGUS_INTEGRITY_ANCHOR_KEY_FILE is required in production.")
    elif not key_file.is_file():
        errors.append("ATLAS_ARGUS_INTEGRITY_ANCHOR_KEY_FILE must be a readable file.")
    else:
        try:
            mode = stat.S_IMODE(key_file.stat().st_mode)
            key = key_file.read_bytes().strip()
        except OSError:
            errors.append("ATLAS_ARGUS_INTEGRITY_ANCHOR_KEY_FILE must be readable.")
        else:
            if mode & 0o077:
                errors.append(
                    "ATLAS_ARGUS_INTEGRITY_ANCHOR_KEY_FILE must not be accessible "
                    "by group or other users."
                )
            if len(key) < 32:
                errors.append(
                    "ATLAS_ARGUS_INTEGRITY_ANCHOR_KEY_FILE must contain at least "
                    "32 bytes of key material."
                )
    return errors


def production_config_errors(*, require_migration_url: bool = False) -> list[str]:
    """Everything that must NOT reach production. Empty list = clear to boot."""
    errors = production_value_config_errors(require_migration_url=require_migration_url)
    if is_production():
        errors.extend(_pdf_capability_errors())
    return errors


def _pdf_capability_errors() -> list[str]:
    """Refuse to boot production without PDF rendering.

    Packet generation degrades gracefully when the renderer is missing, which
    is right for development. In production that degradation would mean
    disclosure packets quietly reverting to browser print-to-PDF — the exact
    uncontrolled output this replaced. Better to fail at startup, where an
    operator sees it, than at the moment counsel needs to produce.
    """
    from .packet_pdf import pdf_available

    if not pdf_available():
        return [
            "PDF rendering is unavailable: WeasyPrint and its system libraries "
            "(Pango, HarfBuzz, fontconfig) plus document fonts must be installed "
            "for production packet generation."
        ]
    return []


def _ingestion_budget_errors() -> list[str]:
    """Individually valid ingestion limits can still be jointly impossible.

    The extraction child has to hold the decoded PDF, a rendered page raster,
    and OCR working memory at once. If its address-space cap is below a small
    multiple of the largest permitted upload, every real upload dies on an
    allocation the operator never sees coming. Refusing to boot is the honest
    failure.
    """
    errors: list[str] = []
    # Read through the getters so an unset variable is checked at its default.
    try:
        upload = max_source_upload_bytes()
        child = source_child_memory_limit_bytes()
        concurrency = max_concurrent_source_ingestions()
        container = api_memory_limit_bytes()
    except RuntimeError:
        # A malformed value is already reported by the bounds check above.
        return errors
    required = upload * SOURCE_CHILD_MEMORY_MULTIPLE
    if child < required:
        errors.append(
            "ATLAS_ARGUS_SOURCE_CHILD_MEMORY_LIMIT_BYTES must be at least "
            f"{SOURCE_CHILD_MEMORY_MULTIPLE}x ATLAS_ARGUS_MAX_SOURCE_UPLOAD_BYTES "
            f"({required} bytes) so extraction can hold the decoded document, a "
            f"rendered page, and OCR working memory; got {child}."
        )
    aggregate_required = (
        child * concurrency
        + max_source_request_bytes()
        + max_total_extracted_text_bytes()
        + max_packet_pdf_bytes()
        + API_BASELINE_MEMORY_RESERVE_BYTES
    )
    if container < aggregate_required:
        errors.append(
            "ATLAS_ARGUS_API_MEMORY_LIMIT_BYTES must cover all admitted extraction "
            "children plus the largest request, extracted text, packet render, and "
            f"baseline reserve ({aggregate_required} bytes required; got {container})."
        )
    return errors


def assert_production_config(*, require_migration_url: bool = False) -> None:
    errors = production_config_errors(require_migration_url=require_migration_url)
    if errors:
        raise RuntimeError(
            "Refusing to start with dev defaults in ATLAS_ARGUS_ENV=production:\n- "
            + "\n- ".join(errors)
        )
