"""Ordered deterministic capability responses for offline execution."""

from __future__ import annotations

from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .broker import AdapterResponse, CapabilityOutcomeState
from .contracts import CapabilityRequest


class ReplayStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    capability_name: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    state: CapabilityOutcomeState
    value: dict[str, Any] = Field(default_factory=dict)
    fault_code: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class DeterministicReplayAdapter:
    def __init__(self, steps: tuple[ReplayStep, ...]):
        self._remaining = deque(steps)

    async def invoke(self, request: CapabilityRequest) -> AdapterResponse:
        if not self._remaining:
            return AdapterResponse(
                state=CapabilityOutcomeState.REJECTED,
                fault_code="REPLAY_EXHAUSTED",
            )
        expected = self._remaining[0]
        if (
            request.capability_name != expected.capability_name
            or request.operation != expected.operation
            or request.arguments != expected.arguments
        ):
            return AdapterResponse(
                state=CapabilityOutcomeState.REJECTED,
                fault_code="REPLAY_REQUEST_MISMATCH",
                diagnostics={
                    "expected_capability": expected.capability_name,
                    "expected_operation": expected.operation,
                },
            )
        self._remaining.popleft()
        return AdapterResponse(
            state=expected.state,
            value=expected.value,
            fault_code=expected.fault_code,
            diagnostics=expected.diagnostics,
        )

    @property
    def exhausted(self) -> bool:
        return not self._remaining
