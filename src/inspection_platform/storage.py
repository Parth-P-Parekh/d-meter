"""Durable SQLite records and hash-addressed local artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .contracts import CycleContext, EvidenceReference, LifecycleState, PipelineResult
from .settings import PlatformSettings


class StorageFault(RuntimeError):
    pass


class InsufficientDiskSpace(StorageFault):
    pass


class ArtifactLimitExceeded(StorageFault):
    pass


@dataclass(frozen=True)
class DiskHealth:
    free_bytes: int
    warning: bool
    accepts_new_cycles: bool


SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    cycle_id TEXT PRIMARY KEY,
    context_json TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    final_result_json TEXT,
    accepted_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS lifecycle_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT,
    previous_state TEXT,
    new_state TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY(cycle_id) REFERENCES cycles(cycle_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    evidence_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    frame_id TEXT,
    result_correlation_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(cycle_id) REFERENCES cycles(cycle_id)
);

CREATE TABLE IF NOT EXISTS plc_publication_intents (
    intent_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL UNIQUE,
    result_value TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode = 'simulated'),
    created_at TEXT NOT NULL,
    acknowledged_at TEXT,
    FOREIGN KEY(cycle_id) REFERENCES cycles(cycle_id)
);

CREATE TABLE IF NOT EXISTS audit_records (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    role TEXT NOT NULL,
    request_id TEXT NOT NULL,
    action TEXT NOT NULL,
    previous_state TEXT,
    new_state TEXT,
    reason TEXT NOT NULL,
    outcome TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
"""


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat()


class DurableResultStore:
    def __init__(self, settings: PlatformSettings):
        self.settings = settings

    def initialize(self) -> None:
        self.settings.data_root.mkdir(parents=True, exist_ok=True)
        self.settings.artifact_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.settings.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()

    def disk_health(self) -> DiskHealth:
        free_bytes = shutil.disk_usage(self.settings.data_root).free
        return DiskHealth(
            free_bytes=free_bytes,
            warning=free_bytes <= self.settings.low_space_warning_bytes,
            accepts_new_cycles=free_bytes > self.settings.stop_accepting_cycles_bytes,
        )

    def accept_cycle(self, context: CycleContext) -> None:
        health = self.disk_health()
        if not health.accepts_new_cycles:
            raise InsufficientDiskSpace(
                f"new cycles disabled with {health.free_bytes} free bytes"
            )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO cycles(
                    cycle_id, context_json, lifecycle_state, accepted_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    context.cycle_id,
                    context.model_dump_json(),
                    LifecycleState.CYCLE_ACCEPTED.value,
                    context.accepted_at.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO lifecycle_events(
                    cycle_id, previous_state, new_state, reason_code, occurred_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    context.cycle_id,
                    LifecycleState.READY.value,
                    LifecycleState.CYCLE_ACCEPTED.value,
                    "CYCLE_ACCEPTED",
                    utc_now_text(),
                ),
            )
            connection.commit()

    def record_transition(
        self,
        cycle_id: str | None,
        previous_state: LifecycleState,
        new_state: LifecycleState,
        reason_code: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if cycle_id is not None:
                updated = connection.execute(
                    "UPDATE cycles SET lifecycle_state = ? WHERE cycle_id = ?",
                    (new_state.value, cycle_id),
                )
                if updated.rowcount != 1:
                    raise StorageFault(f"unknown cycle_id: {cycle_id}")
            connection.execute(
                """
                INSERT INTO lifecycle_events(
                    cycle_id, previous_state, new_state, reason_code, occurred_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    previous_state.value,
                    new_state.value,
                    reason_code,
                    utc_now_text(),
                ),
            )
            connection.commit()

    def write_artifact(
        self,
        *,
        cycle_id: str,
        evidence_id: str,
        content: bytes,
        media_type: str,
        frame_id: str | None = None,
        result_correlation_id: str | None = None,
    ) -> EvidenceReference:
        with self._connect() as connection:
            total = connection.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM artifacts WHERE cycle_id = ?",
                (cycle_id,),
            ).fetchone()[0]
            if total + len(content) > self.settings.artifact_limit_per_cycle_bytes:
                raise ArtifactLimitExceeded("artifact output exceeds the per-cycle limit")

        digest = hashlib.sha256(content).hexdigest()
        relative_path = Path(digest[:2]) / digest[2:4] / digest
        destination = self.settings.artifact_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary_name: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=destination.parent, delete=False
                ) as temporary:
                    temporary.write(content)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    temporary_name = temporary.name
                os.replace(temporary_name, destination)
            finally:
                if temporary_name and Path(temporary_name).exists():
                    Path(temporary_name).unlink()

        created_at = utc_now_text()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO artifacts(
                        evidence_id, cycle_id, sha256, relative_path, media_type,
                        size_bytes, frame_id, result_correlation_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence_id,
                        cycle_id,
                        digest,
                        str(relative_path),
                        media_type,
                        len(content),
                        frame_id,
                        result_correlation_id,
                        created_at,
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError as error:
            raise StorageFault(str(error)) from error

        return EvidenceReference(
            evidence_id=evidence_id,
            artifact_hash=f"sha256:{digest}",
            media_type=media_type,
            frame_id=frame_id,
            result_correlation_id=result_correlation_id,
        )

    def commit_completion(self, result: PipelineResult) -> None:
        now = utc_now_text()
        evidence_ids = {item.evidence_id for item in result.evidence}
        evidence_ids.update(
            item.evidence_id for step in result.steps for item in step.evidence
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            known_ids = {
                row[0]
                for row in connection.execute(
                    "SELECT evidence_id FROM artifacts WHERE cycle_id = ?",
                    (result.cycle_id,),
                )
            }
            if not evidence_ids.issubset(known_ids):
                connection.rollback()
                raise StorageFault("result refers to evidence that is not durably stored")
            updated = connection.execute(
                """
                UPDATE cycles
                SET lifecycle_state = ?, final_result_json = ?, completed_at = ?
                WHERE cycle_id = ? AND final_result_json IS NULL
                """,
                (
                    LifecycleState.RESULT_PUBLICATION.value,
                    result.model_dump_json(),
                    now,
                    result.cycle_id,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise StorageFault("cycle is unknown or already has a final result")
            connection.execute(
                """
                INSERT INTO lifecycle_events(
                    cycle_id, previous_state, new_state, reason_code, occurred_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    result.cycle_id,
                    LifecycleState.PIPELINE_EXECUTING.value,
                    LifecycleState.RESULT_PUBLICATION.value,
                    result.reason_code,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO plc_publication_intents(
                    cycle_id, result_value, reason_code, mode, created_at
                ) VALUES (?, ?, ?, 'simulated', ?)
                """,
                (result.cycle_id, result.result.value, result.reason_code, now),
            )
            connection.commit()

    def audit(
        self,
        *,
        actor: str,
        role: str,
        request_id: str,
        action: str,
        previous_state: LifecycleState | None,
        new_state: LifecycleState | None,
        reason: str,
        outcome: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_records(
                    actor, role, request_id, action, previous_state, new_state,
                    reason, outcome, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    actor,
                    role,
                    request_id,
                    action,
                    previous_state.value if previous_state else None,
                    new_state.value if new_state else None,
                    reason,
                    outcome,
                    utc_now_text(),
                ),
            )
            connection.commit()

    def read_cycle(self, cycle_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM cycles WHERE cycle_id = ?", (cycle_id,)
            ).fetchone()
            if row is None:
                return None
            value = dict(row)
            value["context"] = json.loads(value.pop("context_json"))
            if value["final_result_json"] is not None:
                value["final_result"] = json.loads(value.pop("final_result_json"))
            return value
