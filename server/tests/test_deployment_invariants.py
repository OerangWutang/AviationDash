"""Deployment facts the application depends on but cannot enforce at runtime.

The ingestion admission limit is per-process. Production is pinned to one API
worker so that limit is also the deployment-wide limit. Nothing in the Python
would notice if someone raised the worker count — the app would keep reporting
the configured concurrency while actually allowing N times as much — so the
maintained deployment example is asserted here instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_COMPOSE = REPO_ROOT / "docker-compose.production.example.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile"
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"


@pytest.fixture(scope="module")
def api_service() -> dict:
    if not PRODUCTION_COMPOSE.exists():
        pytest.skip("production compose example is not present")
    compose = yaml.safe_load(PRODUCTION_COMPOSE.read_text())
    return compose["services"]["api"]


def _command_text(api_service: dict) -> str:
    command = api_service.get("command")
    if command is None:
        return ""
    if isinstance(command, str):
        return command
    return " ".join(str(part) for part in command)


def test_production_api_runs_exactly_one_worker(api_service):
    command = _command_text(api_service)
    assert "uvicorn" in command, "expected the api service to pin its uvicorn command"
    workers = re.search(r"--workers\s+(\d+)", command)
    assert workers is not None, (
        "the production api command must state its worker count explicitly — "
        "an unstated default is exactly how this invariant gets lost"
    )
    assert workers.group(1) == "1", (
        f"production must run one API worker while ingestion admission control is "
        f"process-local; found --workers {workers.group(1)}"
    )


def test_production_api_does_not_take_a_configurable_worker_count(api_service):
    """A `${...}` worker count would let an operator raise it without ever
    touching this file's review."""
    command = _command_text(api_service)
    assert not re.search(r"--workers\s+\$", command)


def test_production_api_has_a_memory_limit(api_service):
    """Admission control bounds concurrency, not what one request allocates."""
    assert api_service.get("mem_limit"), (
        "the ingestion child can allocate up to its rlimit; the container needs "
        "its own ceiling"
    )


def test_production_tmp_has_room_for_extraction_results(api_service):
    tmpfs = " ".join(api_service.get("tmpfs") or [])
    size = re.search(r"size=(\d+)m", tmpfs)
    assert size is not None, "expected a bounded /tmp"
    assert int(size.group(1)) >= 256, (
        "extraction writes its result to /tmp before the parent reads it back"
    )


def test_image_installs_the_pdf_and_ocr_binaries():
    """pdf2image and pytesseract are wrappers; without the binaries every
    scanned page fails."""
    dockerfile = DOCKERFILE.read_text()
    assert "poppler-utils" in dockerfile
    assert "tesseract-ocr" in dockerfile


def test_ci_installs_the_same_binaries():
    """Otherwise the OCR tests skip and CI goes green on a build that cannot
    read a scanned page."""
    workflow = CI_WORKFLOW.read_text()
    assert "poppler-utils" in workflow
    assert "tesseract-ocr" in workflow


def test_ingestion_env_vars_are_passed_through(api_service):
    environment = api_service.get("environment") or {}
    for name in (
        "ATLAS_ARGUS_MAX_SOURCE_UPLOAD_BYTES",
        "ATLAS_ARGUS_MAX_CONCURRENT_SOURCE_INGESTIONS",
        "ATLAS_ARGUS_SOURCE_CHILD_MEMORY_LIMIT_BYTES",
        "ATLAS_ARGUS_MIN_OCR_CONFIDENCE_FOR_AUTO_VERIFY",
    ):
        assert name in environment, f"{name} is not passed through to the API container"


def test_production_api_runs_an_init_to_reap_orphans(api_service):
    """An extraction timeout kills the process group; the dying Poppler and
    Tesseract processes are reparented to PID 1. uvicorn does not reap them,
    so without an init they pile up as zombie entries."""
    assert api_service.get("init") is True, (
        "the api service needs `init: true` so killed OCR subprocesses are reaped"
    )
