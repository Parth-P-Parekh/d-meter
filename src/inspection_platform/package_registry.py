"""Durable package registration, activation, and rollback."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .contracts import LifecycleState
from .packages import PackageArchive, PackageFault, PackageManifest
from .settings import PlatformSettings


REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS registered_packages (
    package_name TEXT NOT NULL,
    semantic_version TEXT NOT NULL,
    archive_sha256 TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    manifest_json TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    PRIMARY KEY(package_name, semantic_version)
);

CREATE TABLE IF NOT EXISTS active_package (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    package_name TEXT NOT NULL,
    semantic_version TEXT NOT NULL,
    archive_sha256 TEXT NOT NULL,
    previous_package_name TEXT,
    previous_semantic_version TEXT,
    previous_archive_sha256 TEXT,
    activated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS package_activation_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL CHECK(action IN ('activate', 'rollback')),
    actor TEXT NOT NULL,
    from_package TEXT,
    to_package TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
"""


class PackageRegistryFault(RuntimeError):
    pass


@dataclass(frozen=True)
class ActivePackage:
    package_name: str
    semantic_version: str
    archive_sha256: str


class PackageRegistry:
    def __init__(self, settings: PlatformSettings):
        self.settings = settings
        self.package_root = settings.data_root / "packages"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.settings.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def initialize(self) -> None:
        self.package_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(REGISTRY_SCHEMA)

    def register(self, source: Path) -> PackageManifest:
        manifest = PackageArchive(source).inspect()
        if manifest.approval is None:
            raise PackageFault("package is not approved")
        archive_sha256 = self._file_sha256(source)
        relative_path = (
            Path(manifest.package_name)
            / manifest.semantic_version
            / f"{archive_sha256}.zip"
        )
        destination = self.package_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            self._durable_copy(source, destination)
            destination.chmod(stat.S_IREAD)
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO registered_packages(
                        package_name, semantic_version, archive_sha256,
                        relative_path, manifest_json, approved_by, registered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        manifest.package_name,
                        manifest.semantic_version,
                        archive_sha256,
                        str(relative_path),
                        manifest.model_dump_json(),
                        manifest.approval.approver,
                        datetime.now(UTC).isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                row = connection.execute(
                    """
                    SELECT archive_sha256 FROM registered_packages
                    WHERE package_name = ? AND semantic_version = ?
                    """,
                    (manifest.package_name, manifest.semantic_version),
                ).fetchone()
                if row is None or row["archive_sha256"] != archive_sha256:
                    raise PackageRegistryFault(
                        "that package name and version already identify different bytes"
                    ) from error
        return manifest

    def activate(
        self,
        *,
        package_name: str,
        semantic_version: str,
        state: LifecycleState,
        actor: str,
        request_id: str,
    ) -> ActivePackage:
        if state is not LifecycleState.IDLE:
            raise PackageRegistryFault("package activation is allowed only in idle")
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            package = self._load_registered(connection, package_name, semantic_version)
            self._revalidate_archive(package)
            current = connection.execute(
                "SELECT * FROM active_package WHERE singleton = 1"
            ).fetchone()
            from_package = self._display(current)
            try:
                connection.execute(
                    """
                    INSERT INTO package_activation_events(
                        request_id, action, actor, from_package, to_package, occurred_at
                    ) VALUES (?, 'activate', ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        actor,
                        from_package,
                        f"{package_name}@{semantic_version}",
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise PackageRegistryFault("request_id has already been used") from error
            connection.execute(
                """
                INSERT INTO active_package(
                    singleton, package_name, semantic_version, archive_sha256,
                    previous_package_name, previous_semantic_version,
                    previous_archive_sha256, activated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    package_name = excluded.package_name,
                    semantic_version = excluded.semantic_version,
                    archive_sha256 = excluded.archive_sha256,
                    previous_package_name = active_package.package_name,
                    previous_semantic_version = active_package.semantic_version,
                    previous_archive_sha256 = active_package.archive_sha256,
                    activated_at = excluded.activated_at
                """,
                (
                    package_name,
                    semantic_version,
                    package["archive_sha256"],
                    current["package_name"] if current else None,
                    current["semantic_version"] if current else None,
                    current["archive_sha256"] if current else None,
                    now,
                ),
            )
            self._insert_audit(
                connection,
                actor=actor,
                request_id=request_id,
                action="package.activate",
                previous=from_package,
                new=f"{package_name}@{semantic_version}",
                now=now,
            )
            connection.commit()
            return ActivePackage(package_name, semantic_version, package["archive_sha256"])

    def rollback(
        self,
        *,
        state: LifecycleState,
        actor: str,
        request_id: str,
    ) -> ActivePackage:
        if state is not LifecycleState.IDLE:
            raise PackageRegistryFault("package rollback is allowed only in idle")
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM active_package WHERE singleton = 1"
            ).fetchone()
            if current is None or current["previous_package_name"] is None:
                raise PackageRegistryFault("no previous approved package is available")
            previous = self._load_registered(
                connection,
                current["previous_package_name"],
                current["previous_semantic_version"],
            )
            self._revalidate_archive(previous)
            from_package = f"{current['package_name']}@{current['semantic_version']}"
            to_package = (
                f"{current['previous_package_name']}@{current['previous_semantic_version']}"
            )
            try:
                connection.execute(
                    """
                    INSERT INTO package_activation_events(
                        request_id, action, actor, from_package, to_package, occurred_at
                    ) VALUES (?, 'rollback', ?, ?, ?, ?)
                    """,
                    (request_id, actor, from_package, to_package, now),
                )
            except sqlite3.IntegrityError as error:
                raise PackageRegistryFault("request_id has already been used") from error
            connection.execute(
                """
                UPDATE active_package SET
                    package_name = previous_package_name,
                    semantic_version = previous_semantic_version,
                    archive_sha256 = previous_archive_sha256,
                    previous_package_name = ?,
                    previous_semantic_version = ?,
                    previous_archive_sha256 = ?,
                    activated_at = ?
                WHERE singleton = 1
                """,
                (
                    current["package_name"],
                    current["semantic_version"],
                    current["archive_sha256"],
                    now,
                ),
            )
            self._insert_audit(
                connection,
                actor=actor,
                request_id=request_id,
                action="package.rollback",
                previous=from_package,
                new=to_package,
                now=now,
            )
            connection.commit()
            return ActivePackage(
                previous["package_name"],
                previous["semantic_version"],
                previous["archive_sha256"],
            )

    def current(self) -> ActivePackage | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM active_package WHERE singleton = 1"
            ).fetchone()
            if row is None:
                return None
            return ActivePackage(
                row["package_name"], row["semantic_version"], row["archive_sha256"]
            )

    def _load_registered(
        self, connection: sqlite3.Connection, name: str, version: str
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM registered_packages
            WHERE package_name = ? AND semantic_version = ?
            """,
            (name, version),
        ).fetchone()
        if row is None:
            raise PackageRegistryFault("package is not registered")
        return row

    def _revalidate_archive(self, package: sqlite3.Row) -> None:
        path = self.package_root / package["relative_path"]
        if self._file_sha256(path) != package["archive_sha256"]:
            raise PackageRegistryFault("registered package bytes have changed")
        PackageArchive(path).inspect()

    @staticmethod
    def _display(row: sqlite3.Row | None) -> str | None:
        if row is None:
            return None
        return f"{row['package_name']}@{row['semantic_version']}"

    @staticmethod
    def _insert_audit(
        connection: sqlite3.Connection,
        *,
        actor: str,
        request_id: str,
        action: str,
        previous: str | None,
        new: str,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_records(
                actor, role, request_id, action, previous_state, new_state,
                reason, outcome, occurred_at
            ) VALUES (?, 'pipeline_activation', ?, ?, ?, ?, 'authorized', 'success', ?)
            """,
            (actor, request_id, action, previous, new, now),
        )

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _durable_copy(source: Path, destination: Path) -> None:
        temporary_name: str | None = None
        try:
            with source.open("rb") as input_file, tempfile.NamedTemporaryFile(
                dir=destination.parent, delete=False
            ) as output_file:
                shutil.copyfileobj(input_file, output_file, 1024 * 1024)
                output_file.flush()
                os.fsync(output_file.fileno())
                temporary_name = output_file.name
            os.replace(temporary_name, destination)
        finally:
            if temporary_name and Path(temporary_name).exists():
                Path(temporary_name).unlink()
