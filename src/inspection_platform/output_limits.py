"""Per-cycle structured event, diagnostic, and worker-output bounds."""

from __future__ import annotations

import json
from typing import Any

from .limits import (
    MAX_DIAGNOSTICS_BYTES,
    MAX_EVENT_BYTES,
    MAX_EVENTS_PER_CYCLE,
    MAX_WORKER_OUTPUT_BYTES,
)


class OutputLimitExceeded(RuntimeError):
    pass


def bounded_json_bytes(value: dict[str, Any], maximum: int, label: str) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise OutputLimitExceeded(f"{label} is not safe JSON") from error
    if len(encoded) > maximum:
        raise OutputLimitExceeded(f"{label} exceeds {maximum} bytes")
    return encoded


class EventBudget:
    def __init__(self) -> None:
        self.count = 0

    def accept(self, event: dict[str, Any]) -> bytes:
        if self.count >= MAX_EVENTS_PER_CYCLE:
            raise OutputLimitExceeded("cycle exceeds 1000 events")
        encoded = bounded_json_bytes(event, MAX_EVENT_BYTES, "event")
        self.count += 1
        return encoded


def validate_diagnostics(diagnostics: dict[str, Any]) -> bytes:
    return bounded_json_bytes(diagnostics, MAX_DIAGNOSTICS_BYTES, "diagnostics")


class WorkerOutputBudget:
    def __init__(self) -> None:
        self.size = 0

    def accept(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > MAX_WORKER_OUTPUT_BYTES:
            raise OutputLimitExceeded("worker output exceeds 1 MB")
