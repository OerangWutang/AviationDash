"""Regression coverage for extraction text-budget enforcement."""

from __future__ import annotations

import json

import pytest

from atlas_argus import ingestion
from atlas_argus.ingestion import ExtractionError, ExtractionLimits


def _limits(*, per_page: int = 100, total: int = 8) -> ExtractionLimits:
    return ExtractionLimits(
        max_pages=10,
        max_text_bytes_per_page=per_page,
        max_total_text_bytes=total,
        ocr_page_timeout_seconds=1,
        overall_timeout_seconds=10,
        child_memory_limit_bytes=64 * 1024 * 1024,
    )


class _Page:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _Reader:
    is_encrypted = False

    def __init__(self, pages: list[_Page]) -> None:
        self.pages = pages


def test_native_pages_cannot_cross_the_document_text_budget(monkeypatch):
    reader = _Reader([_Page("abcdef"), _Page("ghijkl")])
    monkeypatch.setattr("pypdf.PdfReader", lambda _stream: reader)
    monkeypatch.setattr(ingestion, "_tesseract_version", lambda: "tesseract-test")
    monkeypatch.setattr(ingestion, "_looks_like_scan", lambda _page, _text: False)

    result = ingestion.extract_pages(b"%PDF-test", _limits(total=8))

    assert [page.text for page in result.pages] == ["abcdef", "gh"]
    assert result.pages[1].text_truncated is True
    assert sum(len(page.text.encode("utf-8")) for page in result.pages) == 8


def test_failed_ocr_fallback_text_obeys_both_budgets(monkeypatch):
    reader = _Reader([_Page("abcdefgh")])
    monkeypatch.setattr("pypdf.PdfReader", lambda _stream: reader)
    monkeypatch.setattr(ingestion, "_tesseract_version", lambda: "tesseract-test")
    monkeypatch.setattr(ingestion, "_looks_like_scan", lambda _page, _text: True)
    monkeypatch.setattr(
        ingestion,
        "_page_ocr_precheck",
        lambda _page, _available: ("pixel_limit", "test refusal"),
    )

    result = ingestion.extract_pages(b"%PDF-test", _limits(per_page=6, total=4))

    assert result.pages[0].method == "failed"
    assert result.pages[0].text == "abcd"
    assert result.pages[0].text_truncated is True


def test_parent_rejects_aggregate_text_over_budget():
    payload = {
        "schema": ingestion._RESULT_SCHEMA,
        "pages": [
            {"pageNumber": 1, "text": "abcd", "method": "native"},
            {"pageNumber": 2, "text": "efgh", "method": "native"},
        ],
    }

    with pytest.raises(ExtractionError, match="document text exceeded"):
        ingestion._parse_result_payload(
            json.dumps(payload).encode("utf-8"),
            _limits(per_page=4, total=7),
        )
