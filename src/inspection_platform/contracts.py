"""Canonical Phase 1 contracts fixed by the replacement architecture."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LifecycleState(StrEnum):
    STARTUP_RECONCILIATION = "startup_reconciliation"
    IDLE = "idle"
    READY = "ready"
    CYCLE_ACCEPTED = "cycle_accepted"
    PIPELINE_EXECUTING = "pipeline_executing"
    RESULT_PUBLICATION = "result_publication"
    PLC_ACKNOWLEDGEMENT = "plc_acknowledgement"
    COMPLETED = "completed"
    HELD = "held"
    ABORTED = "aborted"
    LATCHED_FAULT = "latched_fault"


class InspectionResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NO_RESULT = "NO_RESULT"
    FAULT = "FAULT"
    ABORTED = "ABORTED"


class FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CycleContext(FrozenContract):
    cycle_id: str = Field(min_length=1)
    part_id: str = Field(min_length=1)
    barcode_identity: str | None = None
    recipe_id: str = Field(min_length=1)
    recipe_version: str = Field(min_length=1)
    pipeline_name: str = Field(min_length=1)
    pipeline_version: str = Field(min_length=1)
    pipeline_checksum: str = Field(min_length=1)
    initiator_identity: str = Field(min_length=1)
    accepted_at: datetime
    monotonic_deadline: float = Field(gt=0)
    clock_synchronized: bool


class CapabilityRequest(FrozenContract):
    cycle_id: str = Field(min_length=1)
    request_sequence: int = Field(ge=0)
    capability_name: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    monotonic_deadline: float = Field(gt=0)
    cancellation_token: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class EvidenceReference(FrozenContract):
    evidence_id: str = Field(min_length=1)
    artifact_hash: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    frame_id: str | None = None
    result_correlation_id: str | None = None


class StepResult(FrozenContract):
    step_id: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    result: InspectionResult
    reason_code: str = Field(min_length=1)
    started_at: datetime
    completed_at: datetime
    duration_ns: int = Field(ge=0)
    evidence: tuple[EvidenceReference, ...] = ()
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def completion_cannot_precede_start(self) -> "StepResult":
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        return self


class PipelineResult(FrozenContract):
    cycle_id: str = Field(min_length=1)
    result: InspectionResult
    reason_code: str = Field(min_length=1)
    steps: tuple[StepResult, ...]
    evidence: tuple[EvidenceReference, ...] = ()
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    pipeline_name: str = Field(min_length=1)
    pipeline_version: str = Field(min_length=1)
    pipeline_checksum: str = Field(min_length=1)
    model_versions: dict[str, str] = Field(default_factory=dict)
    preprocessing_versions: dict[str, str] = Field(default_factory=dict)
    configuration_versions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def steps_are_ordered_and_unique(self) -> "PipelineResult":
        identifiers = [step.step_id for step in self.steps]
        ordinals = [step.ordinal for step in self.steps]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("step_id values must be unique")
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("step ordinals must be unique")
        if ordinals != sorted(ordinals):
            raise ValueError("steps must be ordered by ordinal")
        return self
