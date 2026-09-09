"""Public immutable-package API with approval output safety policy."""

from pathlib import Path

from .package_format import (
    CHECKSUM_PATTERN,
    ENTRYPOINT_PATTERN,
    MAX_PACKAGE_BYTES,
    PACKAGE_NAME_PATTERN,
    SEMANTIC_VERSION_PATTERN,
    CapabilityRequirement,
    ImmutableDependency,
    PackageArchive,
    PackageFault,
    PackageManifest,
    ReleaseApproval,
    approve_package as _approve_package,
)
from .signing import ApprovalSigner


def approve_package(
    source: Path,
    destination: Path,
    signer: ApprovalSigner,
    approver: str,
) -> None:
    """Create one approved archive without replacing any existing release bytes."""
    if destination.exists():
        raise PackageFault("approved package destination already exists")
    _approve_package(source, destination, signer, approver)


__all__ = [
    "CHECKSUM_PATTERN",
    "ENTRYPOINT_PATTERN",
    "MAX_PACKAGE_BYTES",
    "PACKAGE_NAME_PATTERN",
    "SEMANTIC_VERSION_PATTERN",
    "CapabilityRequirement",
    "ImmutableDependency",
    "PackageArchive",
    "PackageFault",
    "PackageManifest",
    "ReleaseApproval",
    "approve_package",
]
