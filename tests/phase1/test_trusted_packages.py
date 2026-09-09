import json
import zipfile

import pytest

from inspection_platform.package_registry import PackageRegistry
from inspection_platform.packages import PackageArchive, PackageFault, approve_package
from inspection_platform.settings import PlatformSettings
from inspection_platform.signing import ApprovalSigner
from inspection_platform.storage import DurableResultStore
from inspection_platform.trusted_packages import TrustedPackageRegistry


def unsigned_package(path):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("pipeline/sample.py", "class SamplePipeline: pass")
        archive.writestr("config.schema.json", "{}")
    with zipfile.ZipFile(path, "r") as archive:
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
        "required_capabilities": [],
        "dependencies": [],
        "approval": None,
    }
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))


def test_package_signed_by_unknown_key_is_rejected(tmp_path) -> None:
    settings = PlatformSettings(data_root=tmp_path / "data")
    DurableResultStore(settings).initialize()
    trusted_signer = ApprovalSigner(settings)
    trusted_signer.create_key()
    registry = TrustedPackageRegistry(PackageRegistry(settings), trusted_signer)
    registry.initialize()

    attacker_signer = ApprovalSigner(PlatformSettings(data_root=tmp_path / "attacker"))
    attacker_signer.create_key()
    unsigned = tmp_path / "unsigned.zip"
    malicious = tmp_path / "malicious.zip"
    unsigned_package(unsigned)
    approve_package(unsigned, malicious, attacker_signer, "attacker")

    with pytest.raises(PackageFault, match="configured approver"):
        registry.register(malicious)
