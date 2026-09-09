import pytest

from inspection_platform.contracts import LifecycleState
from inspection_platform.supervisor import ControlSupervisor, InvalidTransition


def test_nominal_lifecycle_uses_fixed_order() -> None:
    supervisor = ControlSupervisor()
    path = [
        LifecycleState.IDLE,
        LifecycleState.READY,
        LifecycleState.CYCLE_ACCEPTED,
        LifecycleState.PIPELINE_EXECUTING,
        LifecycleState.RESULT_PUBLICATION,
        LifecycleState.PLC_ACKNOWLEDGEMENT,
        LifecycleState.COMPLETED,
        LifecycleState.READY,
    ]

    for state in path:
        supervisor.transition(state)

    assert supervisor.state is LifecycleState.READY


def test_transition_not_in_architecture_is_rejected() -> None:
    supervisor = ControlSupervisor()
    with pytest.raises(InvalidTransition):
        supervisor.transition(LifecycleState.READY)


def test_fault_requires_idle_reconciliation_path() -> None:
    supervisor = ControlSupervisor()
    supervisor.transition(LifecycleState.LATCHED_FAULT)
    with pytest.raises(InvalidTransition):
        supervisor.transition(LifecycleState.READY)
    supervisor.transition(LifecycleState.IDLE)
    assert supervisor.state is LifecycleState.IDLE
