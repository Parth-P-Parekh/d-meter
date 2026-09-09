import asyncio
import json
import time
import zipfile
from datetime import UTC, datetime

import pytest

from inspection_platform.broker import HardwareCapabilityBroker
from inspection_platform.contracts import CycleContext
from inspection_platform.packages import PackageArchive, approve_package
from inspection_platform.settings import PlatformSettings
from inspection_platform.signing import ApprovalSigner
from inspection_platform.worker_runner import SupervisedWorkerRunner, WorkerExecutionFault


NOISY_PIPELINE = b'''\
import sys
from inspection_platform.contracts import InspectionResult, PipelineResult

class NoisyPipeline:
    async def execute(self, context, capabilities):
        sys.stdout.buffer.write(b"x" * (1024 * 1024 + 1))
        sys.stdout.buffer.flush()
        return PipelineResult(
            cycle_id=context.cycle_id,
            result=InspectionResult.NO_RESULT,
            reason_code="SIMULATED_NO_PRODUCT_DECISION",
            steps=(),
            pipeline_name=context.pipeline_name,
            pipeline_version=context.pipeline_version,
            pipeline_checksum=context.pipeline_checksum,
        )
'''


def make_noisy_package(tmp_path, signer):
    unsigned = tmp_path / "noisy-unsigned.zip"
    approved = tmp_path / "noisy-approved.zip"
    with zipfile.ZipFile(unsigned, "w") as archive:
        archive.writestr("pipeline/noisy.py", NOISY_PIPELINE)
        archive.writestr("config.schema.json", '{"type":"object"}')
    with zipfile.ZipFile(unsigned) as archive:
        checksum = PackageArchive.payload_checksum(archive)
    with zipfile.ZipFile(unsigned, "a") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "contract_version": "1.0",
                    "package_format": "zip",
                    "package_name": "noisy-replay",
                    "semantic_version": "1.0.0",
                    "entrypoint": "noisy:NoisyPipeline",
                    "code_checksum": checksum,
                    "configuration_schema_version": "1.0",
                    "compatible_platform_version_range": ">=0.1.0,<0.2.0",
                    "required_capabilities": [],
                    "dependencies": [],
                    "approval": None,
                }
            ),
        )
    approve_package(unsigned, approved, signer, "owner")
    return approved, checksum


def test_noisy_worker_is_drained_without_stalling_and_rejected(tmp_path) -> None:
    async def run() -> None:
        settings = PlatformSettings(
            data_root=tmp_path / "data",
            cycle_deadline_seconds=10,
        )
        signer = ApprovalSigner(settings)
        signer.create_key()
        package, checksum = make_noisy_package(tmp_path, signer)
        context = CycleContext(
            cycle_id="noisy-cycle",
            part_id="part-1",
            recipe_id="sample",
            recipe_version="1.0.0",
            pipeline_name="noisy-replay",
            pipeline_version="1.0.0",
            pipeline_checksum=checksum,
            initiator_identity="owner",
            accepted_at=datetime.now(UTC),
            monotonic_deadline=time.monotonic() + 10,
            clock_synchronized=True,
        )
        broker = HardwareCapabilityBroker(
            declared_capabilities=frozenset(), adapters={}
        )

        with pytest.raises(WorkerExecutionFault, match="worker output exceeds 1 MB"):
            await SupervisedWorkerRunner(settings, signer).execute(
                package_path=package,
                context=context,
                broker=broker,
            )

    asyncio.run(run())
