"""Fixed supervisory lifecycle with no hardware access."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import LifecycleState


class InvalidTransition(ValueError):
    pass


ALLOWED_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.STARTUP_RECONCILIATION: frozenset(
        {LifecycleState.IDLE, LifecycleState.LATCHED_FAULT}
    ),
    LifecycleState.IDLE: frozenset({LifecycleState.READY}),
    LifecycleState.READY: frozenset(
        {LifecycleState.CYCLE_ACCEPTED, LifecycleState.HELD}
    ),
    LifecycleState.CYCLE_ACCEPTED: frozenset(
        {LifecycleState.PIPELINE_EXECUTING, LifecycleState.ABORTED}
    ),
    LifecycleState.PIPELINE_EXECUTING: frozenset(
        {
            LifecycleState.RESULT_PUBLICATION,
            LifecycleState.ABORTED,
            LifecycleState.LATCHED_FAULT,
        }
    ),
    LifecycleState.RESULT_PUBLICATION: frozenset(
        {LifecycleState.PLC_ACKNOWLEDGEMENT, LifecycleState.LATCHED_FAULT}
    ),
    LifecycleState.PLC_ACKNOWLEDGEMENT: frozenset(
        {LifecycleState.COMPLETED, LifecycleState.LATCHED_FAULT}
    ),
    LifecycleState.COMPLETED: frozenset({LifecycleState.READY}),
    LifecycleState.HELD: frozenset({LifecycleState.IDLE}),
    LifecycleState.ABORTED: frozenset({LifecycleState.IDLE}),
    LifecycleState.LATCHED_FAULT: frozenset({LifecycleState.IDLE}),
}


@dataclass
class ControlSupervisor:
    state: LifecycleState = LifecycleState.STARTUP_RECONCILIATION

    def transition(self, target: LifecycleState) -> None:
        if target not in ALLOWED_TRANSITIONS[self.state]:
            raise InvalidTransition(f"transition from {self.state} to {target} is not allowed")
        self.state = target
