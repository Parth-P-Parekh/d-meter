"""Signed immutable recipe releases validated against exact pipeline schemas."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema.validators import validator_for
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .contracts import LifecycleState
from .packages import PackageArchive, PackageFault, ReleaseApproval
from .settings import PlatformSettings
from .signing import ApprovalSigner
from .version import CONTRACT_VERSION


MAX_RECIPE_BYTES = 512 * 1024
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class RecipeFault(RuntimeError):
    pass


class RecipeRelease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["1.0"] = CONTRACT_VERSION
    recipe_id: str
    semantic_version: str
    pipeline_name: str
    pipeline_version: str
    pipeline_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    configuration: dict[str, Any]
    approval: ReleaseApproval | None = None

    @field_validator("recipe_id", "pipeline_name")
    @classmethod
    def name_is_canonical(cls, value: str) -> str:
        if not NAME_PATTERN.fullmatch(value):
            raise ValueError("names must use lowercase words separated by hyphens")
        return value

    @field_validator("semantic_version", "pipeline_version")
    @classmethod
    def version_is_semantic(cls, value: str) -> str:
        if not VERSION_PATTERN.fullmatch(value):
            raise ValueError("versions must have exactly three numeric parts")
        return value

    def approval_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"approval"})


@dataclass(frozen=True)
class ActiveRecipe:
    recipe_id: str
    semantic_version: str
    release_sha256: str
    pipeline_name: str
    pipeline_version: str
    pipeline_checksum: str


RECIPE_SCHEMA = """
CREATE TABLE IF NOT EXISTS recipe_releases (
    recipe_id TEXT NOT NULL,
    semantic_version TEXT NOT NULL,
    release_sha256 TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    release_json TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    PRIMARY KEY(recipe_id, semantic_version)
);

CREATE TABLE IF NOT EXISTS active_recipe (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    recipe_id TEXT NOT NULL,
    semantic_version TEXT NOT NULL,
    release_sha256 TEXT NOT NULL,
    pipeline_name TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    pipeline_checksum TEXT NOT NULL,
    previous_recipe_id TEXT,
    previous_semantic_version TEXT,
    previous_release_sha256 TEXT,
    activated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recipe_activation_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL CHECK(action IN ('activate', 'rollback')),
    actor TEXT NOT NULL,
    from_recipe TEXT,
    to_recipe TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
"""


def approve_recipe(
    source: Path,
    destination: Path,
    signer: ApprovalSigner,
    approver: str,
) -> None:
    if destination.exists():
        raise RecipeFault("approved recipe destination already exists")
    draft = _read_release(source, require_approval=False)
    approval = signer.sign(approver=approver, payload=draft.approval_payload())
    approved = draft.model_copy(
        update={"approval": ReleaseApproval(**approval.to_dict())}
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    if temporary.exists():
        raise RecipeFault("temporary recipe approval output already exists")
    try:
        encoded = _canonical_json(approved.model_dump(mode="json"))
        with temporary.open("xb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        _read_release(temporary, require_approval=True)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


class RecipeRegistry:
    def __init__(self, settings: PlatformSettings, signer: ApprovalSigner):
        self.settings = settings
        self.signer = signer
        self.recipe_root = settings.data_root / "recipes"
        self.package_root = settings.data_root / "packages"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.settings.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def initialize(self) -> None:
        self.recipe_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(RECIPE_SCHEMA)

    def register(self, source: Path) -> RecipeRelease:
        release = _read_release(source, require_approval=True)
        self._require_trusted_key(release)
        schema = self._load_exact_pipeline_schema(release)
        self._validate_configuration(release.configuration, schema)
        encoded = _canonical_json(release.model_dump(mode="json"))
        release_sha256 = hashlib.sha256(encoded).hexdigest()
        relative_path = (
            Path(release.recipe_id)
            / release.semantic_version
            / f"{release_sha256}.json"
        )
        destination = self.recipe_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            self._durable_write(destination, encoded)
            destination.chmod(stat.S_IREAD)
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO recipe_releases(
                        recipe_id, semantic_version, release_sha256,
                        relative_path, release_json, approved_by, registered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        release.recipe_id,
                        release.semantic_version,
                        release_sha256,
                        str(relative_path),
                        encoded.decode("utf-8"),
                        release.approval.approver,
                        datetime.now(UTC).isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                existing = connection.execute(
                    """
                    SELECT release_sha256 FROM recipe_releases
                    WHERE recipe_id = ? AND semantic_version = ?
                    """,
                    (release.recipe_id, release.semantic_version),
                ).fetchone()
                if existing is None or existing["release_sha256"] != release_sha256:
                    raise RecipeFault(
                        "that recipe name and version already identify different bytes"
                    ) from error
        return release

    def activate(
        self,
        *,
        recipe_id: str,
        semantic_version: str,
        state: LifecycleState,
        actor: str,
        request_id: str,
    ) -> ActiveRecipe:
        if state is not LifecycleState.IDLE:
            raise RecipeFault("recipe activation is allowed only in idle")
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            release = self._load_registered(connection, recipe_id, semantic_version)
            model = RecipeRelease.model_validate_json(release["release_json"])
            self._revalidate(model, release)
            self._require_active_pipeline(connection, model)
            current = connection.execute(
                "SELECT * FROM active_recipe WHERE singleton = 1"
            ).fetchone()
            previous_display = self._display(current)
            self._insert_event(
                connection,
                request_id=request_id,
                action="activate",
                actor=actor,
                previous=previous_display,
                new=f"{recipe_id}@{semantic_version}",
                now=now,
            )
            connection.execute(
                """
                INSERT INTO active_recipe(
                    singleton, recipe_id, semantic_version, release_sha256,
                    pipeline_name, pipeline_version, pipeline_checksum,
                    previous_recipe_id, previous_semantic_version,
                    previous_release_sha256, activated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    recipe_id = excluded.recipe_id,
                    semantic_version = excluded.semantic_version,
                    release_sha256 = excluded.release_sha256,
                    pipeline_name = excluded.pipeline_name,
                    pipeline_version = excluded.pipeline_version,
                    pipeline_checksum = excluded.pipeline_checksum,
                    previous_recipe_id = active_recipe.recipe_id,
                    previous_semantic_version = active_recipe.semantic_version,
                    previous_release_sha256 = active_recipe.release_sha256,
                    activated_at = excluded.activated_at
                """,
                (
                    recipe_id,
                    semantic_version,
                    release["release_sha256"],
                    model.pipeline_name,
                    model.pipeline_version,
                    model.pipeline_checksum,
                    current["recipe_id"] if current else None,
                    current["semantic_version"] if current else None,
                    current["release_sha256"] if current else None,
                    now,
                ),
            )
            self._audit(
                connection, actor, request_id, "recipe.activate",
                previous_display, f"{recipe_id}@{semantic_version}", now
            )
            connection.commit()
            return self._active(model, release["release_sha256"])

    def rollback(
        self, *, state: LifecycleState, actor: str, request_id: str
    ) -> ActiveRecipe:
        if state is not LifecycleState.IDLE:
            raise RecipeFault("recipe rollback is allowed only in idle")
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM active_recipe WHERE singleton = 1"
            ).fetchone()
            if current is None or current["previous_recipe_id"] is None:
                raise RecipeFault("no previous approved recipe is available")
            previous = self._load_registered(
                connection,
                current["previous_recipe_id"],
                current["previous_semantic_version"],
            )
            model = RecipeRelease.model_validate_json(previous["release_json"])
            self._revalidate(model, previous)
            self._require_active_pipeline(connection, model)
            from_display = self._display(current)
            to_display = f"{model.recipe_id}@{model.semantic_version}"
            self._insert_event(
                connection, request_id=request_id, action="rollback", actor=actor,
                previous=from_display, new=to_display, now=now
            )
            connection.execute(
                """
                UPDATE active_recipe SET
                    recipe_id = previous_recipe_id,
                    semantic_version = previous_semantic_version,
                    release_sha256 = previous_release_sha256,
                    pipeline_name = ?, pipeline_version = ?, pipeline_checksum = ?,
                    previous_recipe_id = ?, previous_semantic_version = ?,
                    previous_release_sha256 = ?, activated_at = ?
                WHERE singleton = 1
                """,
                (
                    model.pipeline_name,
                    model.pipeline_version,
                    model.pipeline_checksum,
                    current["recipe_id"], current["semantic_version"],
                    current["release_sha256"], now,
                ),
            )
            self._audit(
                connection, actor, request_id, "recipe.rollback",
                from_display, to_display, now
            )
            connection.commit()
            return self._active(model, previous["release_sha256"])

    def current(self) -> ActiveRecipe | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM active_recipe WHERE singleton = 1"
            ).fetchone()
        if row is None:
            return None
        return ActiveRecipe(
            recipe_id=row["recipe_id"],
            semantic_version=row["semantic_version"],
            release_sha256=row["release_sha256"],
            pipeline_name=row["pipeline_name"],
            pipeline_version=row["pipeline_version"],
            pipeline_checksum=row["pipeline_checksum"],
        )

    def _load_exact_pipeline_schema(self, release: RecipeRelease) -> dict[str, Any]:
        with self._connect() as connection:
            package = connection.execute(
                """
                SELECT * FROM registered_packages
                WHERE package_name = ? AND semantic_version = ?
                  AND archive_sha256 IS NOT NULL
                """,
                (release.pipeline_name, release.pipeline_version),
            ).fetchone()
        if package is None:
            raise RecipeFault("the recipe pipeline is not registered")
        archive_path = self.package_root / package["relative_path"]
        manifest = PackageArchive(archive_path).inspect()
        self._require_package_trusted(manifest)
        if manifest.code_checksum != release.pipeline_checksum:
            raise RecipeFault("recipe pipeline checksum does not match registered code")
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                schema = json.loads(archive.read("config.schema.json"))
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as error:
            raise RecipeFault("pipeline configuration schema is invalid") from error
        if not isinstance(schema, dict):
            raise RecipeFault("pipeline configuration schema must be an object")
        return schema

    def _revalidate(self, model: RecipeRelease, row: sqlite3.Row) -> None:
        path = self.recipe_root / row["relative_path"]
        encoded = path.read_bytes()
        if hashlib.sha256(encoded).hexdigest() != row["release_sha256"]:
            raise RecipeFault("registered recipe bytes have changed")
        reread = _read_release(path, require_approval=True)
        self._require_trusted_key(reread)
        self._validate_configuration(model.configuration, self._load_exact_pipeline_schema(model))

    def _require_active_pipeline(self, connection: sqlite3.Connection, release: RecipeRelease) -> None:
        active = connection.execute(
            "SELECT * FROM active_package WHERE singleton = 1"
        ).fetchone()
        if active is None:
            raise RecipeFault("no pipeline package is active")
        if (
            active["package_name"] != release.pipeline_name
            or active["semantic_version"] != release.pipeline_version
            or active["archive_sha256"] is None
        ):
            raise RecipeFault("recipe does not target the active pipeline package")

    def _require_trusted_key(self, release: RecipeRelease) -> None:
        if release.approval is None:
            raise RecipeFault("recipe is not approved")
        trusted = base64.b64encode(self.signer.public_key_path.read_bytes()).decode("ascii")
        if release.approval.public_key_base64 != trusted:
            raise RecipeFault("recipe was not signed by the configured approver key")

    def _require_package_trusted(self, manifest) -> None:
        if manifest.approval is None:
            raise PackageFault("package is not approved")
        trusted = base64.b64encode(self.signer.public_key_path.read_bytes()).decode("ascii")
        if manifest.approval.public_key_base64 != trusted:
            raise RecipeFault("recipe pipeline was not signed by the configured approver key")

    @staticmethod
    def _validate_configuration(configuration: dict[str, Any], schema: dict[str, Any]) -> None:
        _reject_external_references(schema)
        try:
            validator_class = validator_for(schema)
            validator_class.check_schema(schema)
            validator_class(schema).validate(configuration)
        except SchemaError as error:
            raise RecipeFault(f"pipeline configuration schema is invalid: {error.message}") from error
        except ValidationError as error:
            location = "/".join(str(part) for part in error.absolute_path) or "configuration"
            raise RecipeFault(f"recipe configuration is invalid at {location}: {error.message}") from error

    @staticmethod
    def _load_registered(connection, recipe_id: str, version: str):
        row = connection.execute(
            "SELECT * FROM recipe_releases WHERE recipe_id = ? AND semantic_version = ?",
            (recipe_id, version),
        ).fetchone()
        if row is None:
            raise RecipeFault("recipe release is not registered")
        return row

    @staticmethod
    def _insert_event(connection, *, request_id, action, actor, previous, new, now):
        try:
            connection.execute(
                """
                INSERT INTO recipe_activation_events(
                    request_id, action, actor, from_recipe, to_recipe, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (request_id, action, actor, previous, new, now),
            )
        except sqlite3.IntegrityError as error:
            raise RecipeFault("request_id has already been used") from error

    @staticmethod
    def _audit(connection, actor, request_id, action, previous, new, now):
        connection.execute(
            """
            INSERT INTO audit_records(
                actor, role, request_id, action, previous_state, new_state,
                reason, outcome, occurred_at
            ) VALUES (?, 'configuration_approval', ?, ?, ?, ?, 'authorized', 'success', ?)
            """,
            (actor, request_id, action, previous, new, now),
        )

    @staticmethod
    def _display(row) -> str | None:
        return None if row is None else f"{row['recipe_id']}@{row['semantic_version']}"

    @staticmethod
    def _active(model: RecipeRelease, digest: str) -> ActiveRecipe:
        return ActiveRecipe(
            model.recipe_id, model.semantic_version, digest,
            model.pipeline_name, model.pipeline_version, model.pipeline_checksum
        )

    @staticmethod
    def _durable_write(destination: Path, content: bytes) -> None:
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
                temporary_name = output.name
            os.replace(temporary_name, destination)
        finally:
            if temporary_name and Path(temporary_name).exists():
                Path(temporary_name).unlink()


def _read_release(path: Path, *, require_approval: bool) -> RecipeRelease:
    if not path.is_file():
        raise RecipeFault("recipe release does not exist")
    if path.stat().st_size > MAX_RECIPE_BYTES:
        raise RecipeFault("recipe release exceeds 512 KB")
    try:
        release = RecipeRelease.model_validate_json(path.read_bytes())
    except ValueError as error:
        raise RecipeFault(f"recipe release is invalid: {error}") from error
    if require_approval:
        if release.approval is None:
            raise RecipeFault("recipe is not approved")
        ApprovalSigner.verify(release.approval_payload(), release.approval.as_signature())
    return release


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _reject_external_references(value: object) -> None:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and not reference.startswith("#"):
            raise RecipeFault("external configuration schema references are not allowed")
        for nested in value.values():
            _reject_external_references(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_external_references(nested)
