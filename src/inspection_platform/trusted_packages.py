"""Package registry boundary that trusts only the configured approver key."""

from __future__ import annotations

import base64
from pathlib import Path

from .contracts import LifecycleState
from .package_registry import ActivePackage, PackageRegistry
from .packages import PackageArchive, PackageFault, PackageManifest
from .signing import ApprovalSigner


class TrustedPackageRegistry:
    def __init__(self, registry: PackageRegistry, signer: ApprovalSigner):
        self._registry = registry
        self._signer = signer
        self.package_root = registry.package_root

    def initialize(self) -> None:
        self._registry.initialize()

    def register(self, source: Path) -> PackageManifest:
        manifest = PackageArchive(source).inspect()
        self._require_trusted_key(manifest)
        return self._registry.register(source)

    def activate(
        self,
        *,
        package_name: str,
        semantic_version: str,
        state: LifecycleState,
        actor: str,
        request_id: str,
    ) -> ActivePackage:
        self._verify_registered(package_name, semantic_version)
        return self._registry.activate(
            package_name=package_name,
            semantic_version=semantic_version,
            state=state,
            actor=actor,
            request_id=request_id,
        )

    def rollback(
        self,
        *,
        state: LifecycleState,
        actor: str,
        request_id: str,
    ) -> ActivePackage:
        with self._registry._connect() as connection:
            current = connection.execute(
                "SELECT * FROM active_package WHERE singleton = 1"
            ).fetchone()
            if current is not None and current["previous_package_name"] is not None:
                self._verify_registered(
                    current["previous_package_name"],
                    current["previous_semantic_version"],
                )
        return self._registry.rollback(state=state, actor=actor, request_id=request_id)

    def current(self) -> ActivePackage | None:
        return self._registry.current()

    def _verify_registered(self, name: str, version: str) -> None:
        with self._registry._connect() as connection:
            package = self._registry._load_registered(connection, name, version)
            archive_path = self.package_root / package["relative_path"]
        manifest = PackageArchive(archive_path).inspect()
        self._require_trusted_key(manifest)

    def _require_trusted_key(self, manifest: PackageManifest) -> None:
        if manifest.approval is None:
            raise PackageFault("package is not approved")
        trusted_key = base64.b64encode(self._signer.public_key_path.read_bytes()).decode(
            "ascii"
        )
        if manifest.approval.public_key_base64 != trusted_key:
            raise PackageFault("package was not signed by the configured approver key")
