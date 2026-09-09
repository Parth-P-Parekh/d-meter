"""Concurrent bounded draining for supervised worker stdout and stderr."""

from __future__ import annotations

import threading
from typing import BinaryIO

from .output_limits import OutputLimitExceeded, WorkerOutputBudget


class WorkerOutputCapture:
    """Drain both process streams without allowing their combined budget to grow."""

    def __init__(self) -> None:
        self._budget = WorkerOutputBudget()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._fault: OutputLimitExceeded | None = None

    def start(self, *streams: BinaryIO | None) -> None:
        if self._threads:
            raise RuntimeError("worker output capture was already started")
        for stream in streams:
            if stream is None:
                continue
            thread = threading.Thread(
                target=self._drain,
                args=(stream,),
                daemon=True,
                name="inspection-worker-output",
            )
            self._threads.append(thread)
            thread.start()

    def finish(self) -> None:
        for thread in self._threads:
            thread.join(timeout=5)
            if thread.is_alive():
                raise OutputLimitExceeded("worker output stream did not close")
        if self._fault is not None:
            raise self._fault

    def _drain(self, stream: BinaryIO) -> None:
        while chunk := stream.read(64 * 1024):
            with self._lock:
                if self._fault is not None:
                    continue
                try:
                    self._budget.accept(chunk)
                except OutputLimitExceeded as error:
                    self._fault = error
