"""Public protocol implemented by approved pipeline classes."""

from __future__ import annotations

from typing import Protocol

from .broker import CapabilityOutcome
from .contracts import CapabilityRequest, CycleContext, PipelineResult


class InspectionCapabilities(Protocol):
    async def execute(self, request: CapabilityRequest) -> CapabilityOutcome: ...


class InspectionPipeline(Protocol):
    async def execute(
        self,
        context: CycleContext,
        capabilities: InspectionCapabilities,
    ) -> PipelineResult: ...
