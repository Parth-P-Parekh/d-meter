from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from inspection_platform.contracts import (
    EvidenceReference,
    InspectionResult,
    LifecycleState,
    PipelineResult,
    StepResult,
)


def test_canonical_lifecycle_names_are_exact() -> None:
    assert [state.value for state in LifecycleState] == [
        "startup_reconciliation",
        "idle",
        "ready",
        "cycle_accepted",
        "pipeline_executing",
        "result_publication",
        "plc_acknowledgement",
        "completed",
        "held",
        "aborted",
        "latched_fault",
    ]


def test_canonical_results_are_exact() -> None:
    assert [result.value for result in InspectionResult] == [
        "PASS",
        "FAIL",
        "NO_RESULT",
        "FAULT",
        "ABORTED",
    ]


def test_pipeline_steps_must_be_unique_and_ordered() -> None:
    now = datetime.now(UTC)
    evidence = EvidenceReference(
        evidence_id="evidence-1",
        artifact_hash="sha256:example",
        media_type="application/json",
    )
    later = StepResult(
        step_id="later",
        ordinal=1,
        result=InspectionResult.PASS,
        reason_code="STEP_OK",
        started_at=now,
        completed_at=now + timedelta(milliseconds=1),
        duration_ns=1_000_000,
        evidence=(evidence,),
    )
    earlier = later.model_copy(update={"step_id": "earlier", "ordinal": 0})

    with pytest.raises(ValidationError, match="ordered by ordinal"):
        PipelineResult(
            cycle_id="cycle-1",
            result=InspectionResult.PASS,
            reason_code="INSPECTION_PASSED",
            steps=(later, earlier),
            pipeline_name="sample",
            pipeline_version="1.0.0",
            pipeline_checksum="sha256:example",
        )


def test_contracts_reject_unknown_fields_and_mutation() -> None:
    with pytest.raises(ValidationError):
        EvidenceReference(
            evidence_id="evidence-1",
            artifact_hash="sha256:example",
            media_type="application/json",
            filesystem_path="forbidden",
        )

    reference = EvidenceReference(
        evidence_id="evidence-1",
        artifact_hash="sha256:example",
        media_type="application/json",
    )
    with pytest.raises(ValidationError):
        reference.media_type = "text/plain"
