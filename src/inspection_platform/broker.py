"""Capability broker with declaration checks, deadlines, cancellation, and idempotency."""

from __future__ import annotations

import asyncio
import hashlib
import time
from contextlib import suppress
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import CapabilityRequest


class CapabilityOutcomeState(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    FAULTED = "FAULTED"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AdapterResponse(FrozenModel):
    state: CapabilityOutcomeState
    value: dict[str, Any] = Field(default_factory=dict)
    fault_code: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fault_code_matches_state(self) -> "AdapterResponse":
        if self.state is CapabilityOutcomeState.SUCCEEDED and self.fault_code is not None:
            raise ValueError("a successful outcome cannot have a fault code")
        if self.state is not CapabilityOutcomeState.SUCCEEDED and not self.fault_code:
            raise ValueError("a non-success outcome requires a fault code")
        return self


class CapabilityOutcome(AdapterResponse):
    cycle_id: str = Field(min_length=1)
    request_sequence: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1)


class CapabilityAdapter(Protocol):
    async def invoke(self, request: CapabilityRequest) -> AdapterResponse: ...


class CancellationRegistry:
    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    def event(self, token: str) -> asyncio.Event:
        return self._events.setdefault(token, asyncio.Event())

    def cancel(self, token: str) -> None:
        self.event(token).set()

    def release(self, token: str) -> None:
        self._events.pop(token, None)


class HardwareCapabilityBroker:
    def __init__(
        self,
        *,
        declared_capabilities: frozenset[str],
        adapters: dict[str, CapabilityAdapter],
        cancellations: CancellationRegistry | None = None,
    ) -> None:
        self._declared = declared_capabilities
        self._adapters = dict(adapters)
        self.cancellations = cancellations or CancellationRegistry()
        self._completed: dict[str, tuple[str, CapabilityOutcome]] = {}
        self._in_flight: set[str] = set()
        self._lock = asyncio.Lock()

    async def execute(self, request: CapabilityRequest) -> CapabilityOutcome:
        fingerprint = hashlib.sha256(
            request.model_dump_json().encode("utf-8")
        ).hexdigest()
        async with self._lock:
            completed = self._completed.get(request.idempotency_key)
            if completed is not None:
                earlier_fingerprint, earlier_outcome = completed
                if earlier_fingerprint == fingerprint:
                    return earlier_outcome
                return self._outcome(
                    request,
                    CapabilityOutcomeState.REJECTED,
                    "IDEMPOTENCY_CONFLICT",
                )
            if request.idempotency_key in self._in_flight:
                return self._outcome(
                    request,
                    CapabilityOutcomeState.REJECTED,
                    "REQUEST_ALREADY_IN_FLIGHT",
                )
            self._in_flight.add(request.idempotency_key)

        try:
            outcome = await self._execute_once(request)
            async with self._lock:
                self._completed[request.idempotency_key] = (fingerprint, outcome)
            return outcome
        finally:
            async with self._lock:
                self._in_flight.discard(request.idempotency_key)

    async def _execute_once(self, request: CapabilityRequest) -> CapabilityOutcome:
        if request.capability_name not in self._declared:
            return self._outcome(
                request,
                CapabilityOutcomeState.REJECTED,
                "CAPABILITY_NOT_DECLARED",
            )
        adapter = self._adapters.get(request.capability_name)
        if adapter is None:
            return self._outcome(
                request,
                CapabilityOutcomeState.FAULTED,
                "CAPABILITY_UNAVAILABLE",
            )
        remaining = request.monotonic_deadline - time.monotonic()
        if remaining <= 0:
            return self._outcome(
                request,
                CapabilityOutcomeState.TIMED_OUT,
                "DEADLINE_EXPIRED",
            )
        cancellation = self.cancellations.event(request.cancellation_token)
        if cancellation.is_set():
            return self._outcome(
                request,
                CapabilityOutcomeState.CANCELLED,
                "REQUEST_CANCELLED",
            )

        adapter_task = asyncio.create_task(adapter.invoke(request))
        cancellation_task = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {adapter_task, cancellation_task},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancellation_task in done:
                adapter_task.cancel()
                with suppress(asyncio.CancelledError):
                    await adapter_task
                return self._outcome(
                    request,
                    CapabilityOutcomeState.CANCELLED,
                    "REQUEST_CANCELLED",
                )
            if adapter_task not in done:
                adapter_task.cancel()
                with suppress(asyncio.CancelledError):
                    await adapter_task
                return self._outcome(
                    request,
                    CapabilityOutcomeState.TIMED_OUT,
                    "DEADLINE_EXPIRED",
                )
            try:
                adapter_response = adapter_task.result()
                if not isinstance(adapter_response, AdapterResponse):
                    raise TypeError("adapter returned an invalid response type")
            except Exception as error:
                return self._outcome(
                    request,
                    CapabilityOutcomeState.FAULTED,
                    "CAPABILITY_EXCEPTION",
                    diagnostics={"exception_type": type(error).__name__},
                )
            return CapabilityOutcome(
                cycle_id=request.cycle_id,
                request_sequence=request.request_sequence,
                idempotency_key=request.idempotency_key,
                **adapter_response.model_dump(),
            )
        finally:
            cancellation_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancellation_task

    @staticmethod
    def _outcome(
        request: CapabilityRequest,
        state: CapabilityOutcomeState,
        fault_code: str,
        diagnostics: dict[str, Any] | None = None,
    ) -> CapabilityOutcome:
        return CapabilityOutcome(
            cycle_id=request.cycle_id,
            request_sequence=request.request_sequence,
            idempotency_key=request.idempotency_key,
            state=state,
            fault_code=fault_code,
            diagnostics=diagnostics or {},
        )
