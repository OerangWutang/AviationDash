"""Admission controls acquired only after authentication and validation."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from fastapi import HTTPException

from ..config import max_concurrent_packet_renders, max_concurrent_source_ingestions


class RequestAdmission:
    def __init__(self, limit: Callable[[], int]) -> None:
        self._lock = threading.Lock()
        self._in_flight = 0
        self._limit = limit

    def try_acquire(self) -> bool:
        with self._lock:
            if self._in_flight >= self._limit():
                return False
            self._in_flight += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight


ingestion_admission = RequestAdmission(max_concurrent_source_ingestions)
source_body_admission = RequestAdmission(max_concurrent_source_ingestions)
packet_render_admission = RequestAdmission(max_concurrent_packet_renders)


@contextmanager
def _slot(admission: RequestAdmission, detail: str) -> Iterator[None]:
    if not admission.try_acquire():
        raise HTTPException(status_code=503, detail=detail)
    try:
        yield
    finally:
        admission.release()


def source_ingestion_slot():
    return _slot(
        ingestion_admission,
        "Too many documents are being processed right now. Try again shortly.",
    )


def packet_render_slot():
    return _slot(
        packet_render_admission,
        "Another evidence packet is being rendered. Try again shortly.",
    )
