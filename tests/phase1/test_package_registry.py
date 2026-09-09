import json
import zipfile

import pytest

from inspection_platform.contracts import LifecycleState
from inspection_platform.package_registry import PackageRegistry, PackageRegistryFault
from inspection_platform.packages import PackageArchive, approve_package
from inspection_platform.settings import PlatformSettings
from inspection_platform.signing import ApprovalSigner
from inspection_platform.storage import DurableResultStore


def make_approved_package(root, version):
    unsigned = root / f"unsigned-{version}.zip"
    approved = root / f"approved-{version}.zip"
    entries = {
        "pipeline/sample.py": b"class SamplePipeline:\n    pass\n",
        "config.schema.json": b'{"type":"object","additionalProperties":false}',
    }
    with zipfile.ZipFile(unsigned, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    with zipfile.ZipFile(unsigned, "r") as archive:
        checksum = PackageArchive.payload_checksum(archive)
    manifest = {
        "contract_version": "1.0",
        "package_format": "zip",
        "package_name": "sample-replay",
        "semantic_version": version,
        "entrypoint": "sample:SamplePipeline",
        "code_checksum": checksum,
        "configuration_schema_version": "1.0",
        "compatible_platform_version_range": ">=0.1.0,<0.2.0",
        "required_capabilities": [],
        "dependencies": [],
        "approval": None,
    }
    with zipfile.ZipFile(unsigned, "a") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
    return unsigned, approved


def make_registry(tmp_path):
    settings = PlatformSettings(data_root=tmp_path / "data")
    DurableResultStore(settings).initialize()
    registry = PackageRegistry(settings)
    registry.initialize()
    signer = ApprovalSigner(settings)
    signer.create_key()
    return registry, signer


def register_version(tmp_path, registry, signer, version):
    unsigned, approved = make_approved_package(tmp_path, version)
    approve_package(unsigned, approved, signer, "owner")
    registry.register(approved)


def test_activation_requires_idle_and_rejects_replayed_command(tmp_path) -> None:
    registry, signer = make_registry(tmp_path)
    register_version(tmp_path, registry, signer, "1.0.0")

    with pytest.raises(PackageRegistryFault, match="only in idle"):
        registry.activate(
            package_name="sample-replay",
            semantic_version="1.0.0",
            state=LifecycleState.READY,
            actor="owner",
            request_id="activate-1",
        )
    active = registry.activate(
        package_name="sample-replay",
        semantic_version="1.0.0",
        state=LifecycleState.IDLE,
        actor="owner",
        request_id="activate-1",
    )
    assert active.semantic_version == "1.0.0"
    with pytest.raises(PackageRegistryFault, match="already been used"):
        registry.activate(
            package_name="sample-replay",
            semantic_version="1.0.0",
            state=LifecycleState.IDLE,
            actor="owner",
            request_id="activate-1",
        )


def test_one_command_rollback_swaps_to_previous_approved_package(tmp_path) -> None:
    registry, signer = make_registry(tmp_path)
    register_version(tmp_path, registry, signer, "1.0.0")
    register_version(tmp_path, registry, signer, "1.1.0")
    registry.activate(
        package_name="sample-replay",
        semantic_version="1.0.0",
        state=LifecycleState.IDLE,
        actor="owner",
        request_id="activate-1",
    )
    registry.activate(
        package_name="sample-replay",
        semantic_version="1.1.0",
        state=LifecycleState.IDLE,
        actor="owner",
        request_id="activate-2",
    )

    active = registry.rollback(
        state=LifecycleState.IDLE, actor="owner", request_id="rollback-1"
    )
    assert active.semantic_version == "1.0.0"
    assert registry.current() == active


def test_registration_is_immutable_for_name_and_version(tmp_path) -> None:
    registry, signer = make_registry(tmp_path)
    register_version(tmp_path, registry, signer, "1.0.0")
    unsigned, approved = make_approved_package(tmp_path, "1.0.0")
    with zipfile.ZipFile(unsigned, "a") as archive:
        archive.writestr("models/extra.bin", b"different")
    with pytest.raises(Exception):
        approve_package(unsigned, approved, signer, "owner")
