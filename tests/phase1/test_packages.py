import json
import zipfile

import pytest

from inspection_platform.packages import (
    PackageArchive,
    PackageFault,
    approve_package,
)
from inspection_platform.settings import PlatformSettings
from inspection_platform.signing import ApprovalSigner


def make_unsigned_package(path, *, checksum_override=None, platform_range=">=0.1.0,<0.2.0"):
    entries = {
        "pipeline/sample.py": b"class SamplePipeline:\n    pass\n",
        "config.schema.json": b'{"type":"object","additionalProperties":false}',
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    with zipfile.ZipFile(path, "r") as archive:
        checksum = PackageArchive.payload_checksum(archive)
    manifest = {
        "contract_version": "1.0",
        "package_format": "zip",
        "package_name": "sample-replay",
        "semantic_version": "1.0.0",
        "entrypoint": "sample:SamplePipeline",
        "code_checksum": checksum_override or checksum,
        "configuration_schema_version": "1.0",
        "compatible_platform_version_range": platform_range,
        "required_capabilities": [],
        "dependencies": [],
        "approval": None,
    }
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))


def test_unsigned_package_can_be_inspected_but_not_activated(tmp_path) -> None:
    package = tmp_path / "sample.zip"
    make_unsigned_package(package)

    manifest = PackageArchive(package).inspect(require_approval=False)
    assert manifest.package_name == "sample-replay"
    with pytest.raises(PackageFault, match="not approved"):
        PackageArchive(package).inspect()


def test_approval_seals_exact_payload(tmp_path) -> None:
    package = tmp_path / "sample.zip"
    approved = tmp_path / "approved.zip"
    make_unsigned_package(package)
    signer = ApprovalSigner(PlatformSettings(data_root=tmp_path / "data"))
    signer.create_key()

    approve_package(package, approved, signer, "owner")
    manifest = PackageArchive(approved).inspect()
    assert manifest.approval is not None
    assert manifest.approval.approver == "owner"

    with zipfile.ZipFile(approved, "a") as archive:
        archive.writestr("pipeline/sample.py", "changed = True")
    with pytest.raises(PackageFault, match="duplicate paths"):
        PackageArchive(approved).inspect()


def test_wrong_fingerprint_is_rejected(tmp_path) -> None:
    package = tmp_path / "wrong.zip"
    make_unsigned_package(package, checksum_override="sha256:" + "0" * 64)

    with pytest.raises(PackageFault, match="fingerprint"):
        PackageArchive(package).inspect(require_approval=False)


def test_path_escape_and_incompatible_platform_are_rejected(tmp_path) -> None:
    escaped = tmp_path / "escaped.zip"
    with zipfile.ZipFile(escaped, "w") as archive:
        archive.writestr("../escape.py", "bad")
    with pytest.raises(PackageFault, match="escapes"):
        PackageArchive(escaped).inspect(require_approval=False)

    incompatible = tmp_path / "incompatible.zip"
    make_unsigned_package(incompatible, platform_range=">=2.0.0")
    with pytest.raises(PackageFault, match="incompatible"):
        PackageArchive(incompatible).inspect(require_approval=False)


def test_expanded_package_limit_is_enforced(tmp_path) -> None:
    package = tmp_path / "large.zip"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pipeline/large.py", b"x" * 10_000)
        archive.writestr("manifest.json", "{}")
        archive.writestr("config.schema.json", "{}")

    with pytest.raises(PackageFault, match="expanded package"):
        PackageArchive(package, max_bytes=1_000).inspect(require_approval=False)
