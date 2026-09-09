import json
import zipfile

import pytest

from inspection_platform.packages import PackageArchive, PackageFault, approve_package
from inspection_platform.settings import PlatformSettings
from inspection_platform.signing import ApprovalSigner


def test_approval_never_replaces_existing_destination(tmp_path) -> None:
    unsigned = tmp_path / "unsigned.zip"
    destination = tmp_path / "approved.zip"
    with zipfile.ZipFile(unsigned, "w") as archive:
        archive.writestr("pipeline/sample.py", "class SamplePipeline: pass")
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
        "required_capabilities": [],
        "dependencies": [],
        "approval": None,
    }
    with zipfile.ZipFile(unsigned, "a") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
    signer = ApprovalSigner(PlatformSettings(data_root=tmp_path / "data"))
    signer.create_key()
    destination.write_bytes(b"existing approved bytes")

    with pytest.raises(PackageFault, match="already exists"):
        approve_package(unsigned, destination, signer, "owner")

    assert destination.read_bytes() == b"existing approved bytes"
