import asyncio
import json
import time
import zipfile
from datetime import UTC, datetime

from inspection_platform.broker import CapabilityOutcomeState, HardwareCapabilityBroker
from inspection_platform.contracts import CycleContext, InspectionResult
from inspection_platform.packages import PackageArchive, approve_package
from inspection_platform.replay import DeterministicReplayAdapter, ReplayStep
from inspection_platform.settings import PlatformSettings
from inspection_platform.signing import ApprovalSigner
from inspection_platform.worker_runner import SupervisedWorkerRunner


PIPELINE_CODE = b'''\
from datetime import UTC, datetime
from inspection_platform.broker import CapabilityOutcomeState
from inspection_platform.contracts import CapabilityRequest, InspectionResult, PipelineResult, StepResult

class SamplePipeline:
    async def execute(self, context, capabilities):
        started = datetime.now(UTC)
        outcome = await capabilities.execute(CapabilityRequest(
            cycle_id=context.cycle_id,
            request_sequence=0,
            capability_name="vision",
            operation="replay_observation",
            idempotency_key=f"{context.cycle_id}:vision:0",
            monotonic_deadline=context.monotonic_deadline,
            cancellation_token=f"{context.cycle_id}:cancel",
            arguments={"source": "deterministic-replay"},
        ))
        completed = datetime.now(UTC)
        reason = "SIMULATED_NO_PRODUCT_DECISION"
        if outcome.state is not CapabilityOutcomeState.SUCCEEDED:
            reason = "SIMULATED_CAPABILITY_NOT_AVAILABLE"
        step = StepResult(
            step_id="simulated-observation",
            ordinal=0,
            result=InspectionResult.NO_RESULT,
            reason_code=reason,
            started_at=started,
            completed_at=completed,
            duration_ns=max(0, int((completed-started).total_seconds()*1_000_000_000)),
        )
        return PipelineResult(
            cycle_id=context.cycle_id,
            result=InspectionResult.NO_RESULT,
            reason_code=reason,
            steps=(step,),
            pipeline_name=context.pipeline_name,
            pipeline_version=context.pipeline_version,
            pipeline_checksum=context.pipeline_checksum,
        )
'''


def make_package(tmp_path, signer):
    unsigned = tmp_path / "unsigned.zip"
    approved = tmp_path / "approved.zip"
    with zipfile.ZipFile(unsigned, "w") as archive:
        archive.writestr("pipeline/sample.py", PIPELINE_CODE)
        archive.writestr("config.schema.json", '{"type":"object"}')
    with zipfile.ZipFile(unsigned, "r") as archive:
        checksum = PackageArchive.payload_checksum(archive)
    manifest = {
        "contract_version": "1.0",
        "package_format": "zip",
        "package_name": "sample-replay",
        "semantic_version": "1.0.0",
        "entrypoint": "sample:SamplePipeline",
        "code_checksum": checksum,
        "configuration_schema_version": "1.0",
        "compatible_platform_version_range": ">=0.1.0,<0.2.0",
        "required_capabilities": [
            {"name": "vision", "contract_version": "1.0"}
        ],
        "dependencies": [],
        "approval": None,
    }
    with zipfile.ZipFile(unsigned, "a") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
    approve_package(unsigned, approved, signer, "owner")
    return approved, checksum


def test_approved_sample_runs_in_limited_process_and_returns_no_result(tmp_path) -> None:
    async def run():
        settings = PlatformSettings(
            data_root=tmp_path / "data",
            cycle_deadline_seconds=10,
        )
        signer = ApprovalSigner(settings)
        signer.create_key()
        package, checksum = make_package(tmp_path, signer)
        context = CycleContext(
            cycle_id="cycle-1",
            part_id="part-1",
            recipe_id="sample",
            recipe_version="1.0.0",
            pipeline_name="sample-replay",
            pipeline_version="1.0.0",
            pipeline_checksum=checksum,
            initiator_identity="owner",
            accepted_at=datetime.now(UTC),
            monotonic_deadline=time.monotonic() + 10,
            clock_synchronized=True,
        )
        replay = DeterministicReplayAdapter(
            (
                ReplayStep(
                    capability_name="vision",
                    operation="replay_observation",
                    arguments={"source": "deterministic-replay"},
                    state=CapabilityOutcomeState.SUCCEEDED,
                    value={"frame_id": "recorded-frame-1"},
                ),
            )
        )
        broker = HardwareCapabilityBroker(
            declared_capabilities=frozenset({"vision"}),
            adapters={"vision": replay},
        )
        result = await SupervisedWorkerRunner(settings, signer).execute(
            package_path=package,
            context=context,
            broker=broker,
        )
        assert result.result is InspectionResult.NO_RESULT
        assert result.reason_code == "SIMULATED_NO_PRODUCT_DECISION"
        assert replay.exhausted

    asyncio.run(run())
