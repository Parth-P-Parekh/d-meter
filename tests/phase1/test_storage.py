from datetime import UTC, datetime

import pytest

from inspection_platform.contracts import (
    CycleContext,
    EvidenceReference,
    InspectionResult,
    PipelineResult,
    StepResult,
)
from inspection_platform.settings import PlatformSettings
from inspection_platform.storage import (
    ArtifactLimitExceeded,
    DurableResultStore,
    StorageFault,
)


def make_context(cycle_id: str = "cycle-1") -> CycleContext:
    return CycleContext(
        cycle_id=cycle_id,
        part_id="part-1",
        recipe_id="recipe-1",
        recipe_version="1.0.0",
        pipeline_name="sample",
        pipeline_version="1.0.0",
        pipeline_checksum="sha256:sample",
        initiator_identity="approver",
        accepted_at=datetime.now(UTC),
        monotonic_deadline=100.0,
        clock_synchronized=True,
    )


def make_result(evidence: EvidenceReference) -> PipelineResult:
    now = datetime.now(UTC)
    step = StepResult(
        step_id="inspect",
        ordinal=0,
        result=InspectionResult.PASS,
        reason_code="STEP_OK",
        started_at=now,
        completed_at=now,
        duration_ns=0,
        evidence=(evidence,),
    )
    return PipelineResult(
        cycle_id="cycle-1",
        result=InspectionResult.PASS,
        reason_code="INSPECTION_PASSED",
        steps=(step,),
        evidence=(evidence,),
        pipeline_name="sample",
        pipeline_version="1.0.0",
        pipeline_checksum="sha256:sample",
    )


def test_completion_and_publication_intent_are_committed_together(tmp_path) -> None:
    settings = PlatformSettings(
        data_root=tmp_path,
        low_space_warning_bytes=1,
        stop_accepting_cycles_bytes=1,
    )
    store = DurableResultStore(settings)
    store.initialize()
    store.accept_cycle(make_context())
    evidence = store.write_artifact(
        cycle_id="cycle-1",
        evidence_id="evidence-1",
        content=b"exact evidence",
        media_type="application/octet-stream",
    )
    store.commit_completion(make_result(evidence))

    cycle = store.read_cycle("cycle-1")
    assert cycle is not None
    assert cycle["lifecycle_state"] == "result_publication"
    assert cycle["final_result"]["result"] == "PASS"


def test_unknown_evidence_rolls_back_entire_completion(tmp_path) -> None:
    settings = PlatformSettings(
        data_root=tmp_path,
        low_space_warning_bytes=1,
        stop_accepting_cycles_bytes=1,
    )
    store = DurableResultStore(settings)
    store.initialize()
    store.accept_cycle(make_context())
    missing = EvidenceReference(
        evidence_id="missing",
        artifact_hash="sha256:missing",
        media_type="application/octet-stream",
    )

    with pytest.raises(StorageFault, match="not durably stored"):
        store.commit_completion(make_result(missing))

    cycle = store.read_cycle("cycle-1")
    assert cycle is not None
    assert cycle["final_result_json"] is None
    assert cycle["lifecycle_state"] == "cycle_accepted"


def test_artifact_limit_is_enforced_before_write(tmp_path) -> None:
    settings = PlatformSettings(
        data_root=tmp_path,
        artifact_limit_per_cycle_bytes=4,
        low_space_warning_bytes=1,
        stop_accepting_cycles_bytes=1,
    )
    store = DurableResultStore(settings)
    store.initialize()
    store.accept_cycle(make_context())

    with pytest.raises(ArtifactLimitExceeded):
        store.write_artifact(
            cycle_id="cycle-1",
            evidence_id="too-large",
            content=b"12345",
            media_type="application/octet-stream",
        )
