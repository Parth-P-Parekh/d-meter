"""Immutable ZIP pipeline packages and signed manifests."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Literal

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .signing import ApprovalSignature, ApprovalSigner
from .version import CONTRACT_VERSION, PIPELINE_PACKAGE_FORMAT, PLATFORM_VERSION


MAX_PACKAGE_BYTES = 2 * 1024**3
CHECKSUM_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SEMANTIC_VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
PACKAGE_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ENTRYPOINT_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)


class PackageFault(ValueError):
    pass


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CapabilityRequirement(FrozenModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    contract_version: str = Field(min_length=1)


class ImmutableDependency(FrozenModel):
    kind: Literal["model", "preprocessing"]
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    checksum: str

    @field_validator("checksum")
    @classmethod
    def checksum_is_sha256(cls, value: str) -> str:
        if not CHECKSUM_PATTERN.fullmatch(value):
            raise ValueError("checksum must be a lowercase SHA-256 fingerprint")
        return value


class ReleaseApproval(FrozenModel):
    algorithm: Literal["Ed25519-SHA256"]
    approver: str = Field(min_length=1)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    public_key_base64: str = Field(min_length=1)
    signature_base64: str = Field(min_length=1)

    def as_signature(self) -> ApprovalSignature:
        return ApprovalSignature(**self.model_dump())


class PackageManifest(FrozenModel):
    contract_version: Literal["1.0"] = CONTRACT_VERSION
    package_format: Literal["zip"] = PIPELINE_PACKAGE_FORMAT
    package_name: str
    semantic_version: str
    entrypoint: str
    code_checksum: str
    configuration_schema_version: str = Field(min_length=1)
    compatible_platform_version_range: str = Field(min_length=1)
    required_capabilities: tuple[CapabilityRequirement, ...]
    dependencies: tuple[ImmutableDependency, ...] = ()
    approval: ReleaseApproval | None = None

    @field_validator("package_name")
    @classmethod
    def package_name_is_canonical(cls, value: str) -> str:
        if not PACKAGE_NAME_PATTERN.fullmatch(value):
            raise ValueError("package_name must use lowercase words separated by hyphens")
        return value

    @field_validator("semantic_version")
    @classmethod
    def version_is_semantic(cls, value: str) -> str:
        if not SEMANTIC_VERSION_PATTERN.fullmatch(value):
            raise ValueError("semantic_version must have exactly three numeric parts")
        Version(value)
        return value

    @field_validator("entrypoint")
    @classmethod
    def entrypoint_is_module_and_class(cls, value: str) -> str:
        if not ENTRYPOINT_PATTERN.fullmatch(value):
            raise ValueError("entrypoint must be module:class")
        return value

    @field_validator("code_checksum")
    @classmethod
    def code_checksum_is_sha256(cls, value: str) -> str:
        if not CHECKSUM_PATTERN.fullmatch(value):
            raise ValueError("code_checksum must be a lowercase SHA-256 fingerprint")
        return value

    @field_validator("compatible_platform_version_range")
    @classmethod
    def platform_range_is_valid(cls, value: str) -> str:
        try:
            SpecifierSet(value)
        except InvalidSpecifier as error:
            raise ValueError("invalid platform version range") from error
        return value

    @model_validator(mode="after")
    def named_requirements_are_unique(self) -> "PackageManifest":
        names = [item.name for item in self.required_capabilities]
        if len(names) != len(set(names)):
            raise ValueError("required capability names must be unique")
        dependency_keys = [(item.kind, item.name) for item in self.dependencies]
        if len(dependency_keys) != len(set(dependency_keys)):
            raise ValueError("dependency names must be unique within each kind")
        return self

    def approval_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"approval"})

    def supports_current_platform(self) -> bool:
        return Version(PLATFORM_VERSION) in SpecifierSet(
            self.compatible_platform_version_range
        )


class PackageArchive:
    def __init__(self, path: Path, max_bytes: int = MAX_PACKAGE_BYTES):
        self.path = path
        self.max_bytes = max_bytes

    def inspect(self, *, require_approval: bool = True) -> PackageManifest:
        if not self.path.is_file():
            raise PackageFault("package archive does not exist")
        if self.path.stat().st_size > self.max_bytes:
            raise PackageFault("package archive exceeds the size limit")
        try:
            with zipfile.ZipFile(self.path, "r") as archive:
                files = self._validate_entries(archive)
                required = {"manifest.json", "config.schema.json"}
                if not required.issubset(files):
                    raise PackageFault("package is missing manifest.json or config.schema.json")
                if not any(name.startswith("pipeline/") for name in files):
                    raise PackageFault("package has no pipeline code")
                try:
                    manifest = PackageManifest.model_validate_json(
                        archive.read("manifest.json")
                    )
                except (ValueError, json.JSONDecodeError) as error:
                    raise PackageFault(f"invalid package manifest: {error}") from error
                actual_checksum = self.payload_checksum(archive)
                if manifest.code_checksum != actual_checksum:
                    raise PackageFault("package payload fingerprint does not match manifest")
                self._validate_entrypoint(manifest, files)
                if not manifest.supports_current_platform():
                    raise PackageFault("package is incompatible with this platform version")
                if require_approval:
                    if manifest.approval is None:
                        raise PackageFault("package is not approved")
                    ApprovalSigner.verify(
                        manifest.approval_payload(), manifest.approval.as_signature()
                    )
                return manifest
        except zipfile.BadZipFile as error:
            raise PackageFault("package is not a valid ZIP archive") from error

    def _validate_entries(self, archive: zipfile.ZipFile) -> set[str]:
        names: set[str] = set()
        expanded_size = 0
        allowed_roots = {
            "manifest.json",
            "config.schema.json",
            "pipeline",
            "models",
            "preprocessing",
        }
        for info in archive.infolist():
            name = info.filename
            if name in names:
                raise PackageFault("package contains duplicate paths")
            names.add(name)
            if info.flag_bits & 0x1:
                raise PackageFault("encrypted package entries are not allowed")
            if "\\" in name:
                raise PackageFault("package paths must use forward slashes")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise PackageFault("package path escapes its archive root")
            if path.parts[0] not in allowed_roots:
                raise PackageFault(f"package path is not allowed: {name}")
            unix_mode = info.external_attr >> 16
            if unix_mode and stat.S_ISLNK(unix_mode):
                raise PackageFault("symbolic links are not allowed")
            if not info.is_dir():
                expanded_size += info.file_size
                if expanded_size > self.max_bytes:
                    raise PackageFault("expanded package exceeds the size limit")
        return {name for name in names if not name.endswith("/")}

    @staticmethod
    def payload_checksum(archive: zipfile.ZipFile) -> str:
        digest = hashlib.sha256()
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            if info.is_dir() or info.filename == "manifest.json":
                continue
            digest.update(info.filename.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(info.file_size).encode("ascii"))
            digest.update(b"\0")
            with archive.open(info, "r") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    @staticmethod
    def _validate_entrypoint(manifest: PackageManifest, files: set[str]) -> None:
        module_name, _ = manifest.entrypoint.split(":", 1)
        module_path = "pipeline/" + module_name.replace(".", "/")
        if f"{module_path}.py" not in files and f"{module_path}/__init__.py" not in files:
            raise PackageFault("entrypoint module is not present in pipeline code")


def approve_package(source: Path, destination: Path, signer: ApprovalSigner, approver: str) -> None:
    source_archive = PackageArchive(source)
    manifest = source_archive.inspect(require_approval=False)
    approval = signer.sign(approver=approver, payload=manifest.approval_payload())
    approved_manifest = manifest.model_copy(
        update={"approval": ReleaseApproval(**approval.to_dict())}
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    if temporary.exists():
        raise PackageFault("temporary approval output already exists")
    try:
        with zipfile.ZipFile(source, "r") as existing, zipfile.ZipFile(
            temporary, "w", allowZip64=True
        ) as approved:
            approved.writestr(
                "manifest.json",
                json.dumps(
                    approved_manifest.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            for info in existing.infolist():
                if info.filename == "manifest.json":
                    continue
                with existing.open(info, "r") as source_file, approved.open(
                    info, "w", force_zip64=True
                ) as destination_file:
                    shutil.copyfileobj(source_file, destination_file, 1024 * 1024)
        PackageArchive(temporary).inspect(require_approval=True)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()
