"""Durable restart review and conservative offline reconciliation."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from .contracts import CycleContext, LifecycleState
from .storage import DurableResultStore, InsufficientDiskSpace, utc_now_text
from .supervisor import ControlSupervisor


class RecoveryFault(RuntimeError):
    pass


class RecoveryDecision(StrEnum):
    DISMISS = "dismiss"
    RERUN = "rerun"


@dataclass(frozen=True)
class RecoveryReview:
    review_id: str
    original_cycle_id: str
    interrupted_state: LifecycleState
    decision: RecoveryDecision | None
    detected_at: str
    decided_at: str | None
    decided_by: str | None
    replacement_cycle_id: str | None
    reset_completed_at: str | None


RECOVERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS recovery_reviews (
    review_id TEXT PRIMARY KEY,
    original_cycle_id TEXT NOT NULL UNIQUE,
    interrupted_state TEXT NOT NULL,
    original_context_json TEXT NOT NULL,
    decision TEXT CHECK(decision IN ('dismiss', 'rerun')),
    detected_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    decision_request_id TEXT UNIQUE,
    replacement_cycle_id TEXT UNIQUE,
    reset_completed_at TEXT,
    reset_by TEXT,
    reset_request_id TEXT UNIQUE,
    FOREIGN KEY(original_cycle_id) REFERENCES cycles(cycle_id),
    FOREIGN KEY(replacement_cycle_id) REFERENCES cycles(cycle_id)
);
"""


INTERRUPTIBLE_STATES = (
    LifecycleState.CYCLE_ACCEPTED,
    LifecycleState.PIPELINE_EXECUTING,
    LifecycleState.RESULT_PUBLICATION,
    LifecycleState.PLC_ACKNOWLEDGEMENT,
)


class RecoveryReviewQueue:
    def __init__(self, store: DurableResultStore):
        self.store = store

    def initialize(self) -> None:
        with self.store._connect() as connection:
            connection.executescript(RECOVERY_SCHEMA)

    def discover_after_restart(self, supervisor: ControlSupervisor) -> tuple[RecoveryReview, ...]:
        if supervisor.state is not LifecycleState.STARTUP_RECONCILIATION:
            raise RecoveryFault("restart discovery requires startup_reconciliation")
        state_values = tuple(state.value for state in INTERRUPTIBLE_STATES)
        placeholders = ",".join("?" for _ in state_values)
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"""
                SELECT cycle_id, lifecycle_state, context_json
                FROM cycles
                WHERE lifecycle_state IN ({placeholders})
                  AND NOT EXISTS (
                    SELECT 1 FROM recovery_reviews
                    WHERE original_cycle_id = cycles.cycle_id
                  )
                ORDER BY accepted_at, cycle_id
                """,
                state_values,
            ).fetchall()
            detected_at = utc_now_text()
            for row in rows:
                connection.execute(
                    """
                    INSERT INTO recovery_reviews(
                        review_id, original_cycle_id, interrupted_state,
                        original_context_json, detected_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        row["cycle_id"],
                        row["lifecycle_state"],
                        row["context_json"],
                        detected_at,
                    ),
                )
            outstanding = connection.execute(
                "SELECT COUNT(*) FROM recovery_reviews WHERE reset_completed_at IS NULL"
            ).fetchone()[0]
            connection.commit()
        supervisor.transition(
            LifecycleState.LATCHED_FAULT if outstanding else LifecycleState.IDLE
        )
        return self.list_reviews(outstanding_only=True)

    def list_reviews(self, *, outstanding_only: bool = False) -> tuple[RecoveryReview, ...]:
        where = "WHERE reset_completed_at IS NULL" if outstanding_only else ""
        with self.store._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM recovery_reviews {where} ORDER BY detected_at, review_id"
            ).fetchall()
        return tuple(self._to_review(row) for row in rows)

    def decide(
        self,
        *,
        review_id: str,
        decision: RecoveryDecision,
        actor: str,
        request_id: str,
    ) -> RecoveryReview:
        now = utc_now_text()
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM recovery_reviews WHERE review_id = ?", (review_id,)
            ).fetchone()
            if row is None:
                raise RecoveryFault("recovery review does not exist")
            if row["decision"] is not None:
                raise RecoveryFault("recovery review already has a decision")
            if row["reset_completed_at"] is not None:
                raise RecoveryFault("recovery review is already reset")
            try:
                connection.execute(
                    """
                    UPDATE recovery_reviews SET
                        decision = ?, decided_at = ?, decided_by = ?,
                        decision_request_id = ?
                    WHERE review_id = ?
                    """,
                    (decision.value, now, actor, request_id, review_id),
                )
                self._audit(
                    connection,
                    actor=actor,
                    request_id=request_id,
                    action=f"recovery.{decision.value}",
                    previous="pending",
                    new=decision.value,
                    now=now,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            updated = connection.execute(
                "SELECT * FROM recovery_reviews WHERE review_id = ?", (review_id,)
            ).fetchone()
        return self._to_review(updated)

    def reset(
        self,
        *,
        supervisor: ControlSupervisor,
        actor: str,
        request_id: str,
    ) -> CycleContext | None:
        if supervisor.state is not LifecycleState.LATCHED_FAULT:
            raise RecoveryFault("recovery reset requires latched_fault")
        health = self.store.disk_health()
        if not health.accepts_new_cycles:
            raise InsufficientDiskSpace(
                f"recovery reset cannot create a rerun with {health.free_bytes} free bytes"
            )
        now = datetime.now(UTC)
        now_text = now.isoformat()
        replacement: CycleContext | None = None
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            outstanding = connection.execute(
                """
                SELECT * FROM recovery_reviews
                WHERE reset_completed_at IS NULL
                ORDER BY detected_at, review_id
                """
            ).fetchall()
            if not outstanding:
                raise RecoveryFault("no recovery reviews require reset")
            if any(row["decision"] is None for row in outstanding):
                raise RecoveryFault("every recovery review requires a decision before reset")
            reruns = [row for row in outstanding if row["decision"] == RecoveryDecision.RERUN.value]
            if len(reruns) > 1:
                raise RecoveryFault("only one interrupted cycle can be rerun at a time")
            if reruns:
                original = CycleContext.model_validate_json(
                    reruns[0]["original_context_json"]
                )
                replacement = original.model_copy(
                    update={
                        "cycle_id": str(uuid.uuid4()),
                        "initiator_identity": actor,
                        "accepted_at": now,
                        "monotonic_deadline": time.monotonic()
                        + self.store.settings.cycle_deadline_seconds,
                    }
                )
                connection.execute(
                    """
                    INSERT INTO cycles(
                        cycle_id, context_json, lifecycle_state, accepted_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        replacement.cycle_id,
                        replacement.model_dump_json(),
                        LifecycleState.CYCLE_ACCEPTED.value,
                        now_text,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO lifecycle_events(
                        cycle_id, previous_state, new_state, reason_code, occurred_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        replacement.cycle_id,
                        LifecycleState.READY.value,
                        LifecycleState.CYCLE_ACCEPTED.value,
                        "RECOVERY_RERUN_ACCEPTED",
                        now_text,
                    ),
                )
                connection.execute(
                    """
                    UPDATE recovery_reviews SET replacement_cycle_id = ?
                    WHERE review_id = ?
                    """,
                    (replacement.cycle_id, reruns[0]["review_id"]),
                )
            connection.execute(
                """
                UPDATE recovery_reviews SET
                    reset_completed_at = ?, reset_by = ?, reset_request_id = ?
                WHERE reset_completed_at IS NULL
                """,
                (now_text, actor, request_id),
            )
            self._audit(
                connection,
                actor=actor,
                request_id=request_id,
                action="recovery.reset",
                previous=LifecycleState.LATCHED_FAULT.value,
                new=(
                    LifecycleState.CYCLE_ACCEPTED.value
                    if replacement
                    else LifecycleState.IDLE.value
                ),
                now=now_text,
            )
            connection.commit()
        supervisor.transition(LifecycleState.IDLE)
        if replacement is not None:
            supervisor.transition(LifecycleState.READY)
            supervisor.transition(LifecycleState.CYCLE_ACCEPTED)
        return replacement

    @staticmethod
    def _audit(connection, *, actor: str, request_id: str, action: str, previous: str, new: str, now: str) -> None:
        connection.execute(
            """
            INSERT INTO audit_records(
                actor, role, request_id, action, previous_state, new_state,
                reason, outcome, occurred_at
            ) VALUES (?, 'operation', ?, ?, ?, ?, 'authorized recovery review', 'success', ?)
            """,
            (actor, request_id, action, previous, new, now),
        )

    @staticmethod
    def _to_review(row) -> RecoveryReview:
        return RecoveryReview(
            review_id=row["review_id"],
            original_cycle_id=row["original_cycle_id"],
            interrupted_state=LifecycleState(row["interrupted_state"]),
            decision=RecoveryDecision(row["decision"]) if row["decision"] else None,
            detected_at=row["detected_at"],
            decided_at=row["decided_at"],
            decided_by=row["decided_by"],
            replacement_cycle_id=row["replacement_cycle_id"],
            reset_completed_at=row["reset_completed_at"],
        )
