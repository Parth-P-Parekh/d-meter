import pytest

from inspection_platform.limits import MAX_EVENTS_PER_CYCLE
from inspection_platform.output_limits import (
    EventBudget,
    OutputLimitExceeded,
    WorkerOutputBudget,
    validate_diagnostics,
)


def test_event_count_and_size_are_bounded() -> None:
    budget = EventBudget()
    for number in range(MAX_EVENTS_PER_CYCLE):
        budget.accept({"number": number})
    with pytest.raises(OutputLimitExceeded, match="1000 events"):
        budget.accept({"number": MAX_EVENTS_PER_CYCLE})

    with pytest.raises(OutputLimitExceeded, match="event exceeds"):
        EventBudget().accept({"large": "x" * (64 * 1024)})


def test_diagnostics_reject_non_json_and_oversize_values() -> None:
    with pytest.raises(OutputLimitExceeded, match="not safe JSON"):
        validate_diagnostics({"bad": object()})
    with pytest.raises(OutputLimitExceeded, match="diagnostics exceeds"):
        validate_diagnostics({"large": "x" * (64 * 1024)})


def test_worker_output_budget_is_combined() -> None:
    budget = WorkerOutputBudget()
    budget.accept(b"x" * (512 * 1024))
    budget.accept(b"x" * (512 * 1024))
    with pytest.raises(OutputLimitExceeded, match="worker output"):
        budget.accept(b"x")
