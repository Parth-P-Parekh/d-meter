import asyncio
import time

from inspection_platform.broker import (
    AdapterResponse,
    CancellationRegistry,
    CapabilityOutcomeState,
    HardwareCapabilityBroker,
)
from inspection_platform.contracts import CapabilityRequest
from inspection_platform.replay import DeterministicReplayAdapter, ReplayStep


def request(**changes) -> CapabilityRequest:
    values = {
        "cycle_id": "cycle-1",
        "request_sequence": 0,
        "capability_name": "vision",
        "operation": "capture",
        "idempotency_key": "capture-1",
        "monotonic_deadline": time.monotonic() + 5,
        "cancellation_token": "cancel-1",
        "arguments": {"mode": "replay"},
    }
    values.update(changes)
    return CapabilityRequest(**values)


def test_replay_is_deterministic_and_idempotent() -> None:
    async def run():
        adapter = DeterministicReplayAdapter(
            (
                ReplayStep(
                    capability_name="vision",
                    operation="capture",
                    arguments={"mode": "replay"},
                    state=CapabilityOutcomeState.SUCCEEDED,
                    value={"frame_id": "frame-1"},
                ),
            )
        )
        broker = HardwareCapabilityBroker(
            declared_capabilities=frozenset({"vision"}),
            adapters={"vision": adapter},
        )
        first = await broker.execute(request())
        second = await broker.execute(request())
        assert first == second
        assert first.state is CapabilityOutcomeState.SUCCEEDED
        assert adapter.exhausted

    asyncio.run(run())


def test_changed_request_cannot_reuse_idempotency_key() -> None:
    async def run():
        adapter = DeterministicReplayAdapter(
            (
                ReplayStep(
                    capability_name="vision",
                    operation="capture",
                    arguments={"mode": "replay"},
                    state=CapabilityOutcomeState.SUCCEEDED,
                ),
            )
        )
        broker = HardwareCapabilityBroker(
            declared_capabilities=frozenset({"vision"}),
            adapters={"vision": adapter},
        )
        await broker.execute(request())
        changed = await broker.execute(request(arguments={"mode": "changed"}))
        assert changed.state is CapabilityOutcomeState.REJECTED
        assert changed.fault_code == "IDEMPOTENCY_CONFLICT"

    asyncio.run(run())


def test_undeclared_and_expired_requests_never_reach_adapter() -> None:
    async def run():
        adapter = DeterministicReplayAdapter(())
        broker = HardwareCapabilityBroker(
            declared_capabilities=frozenset(), adapters={"vision": adapter}
        )
        undeclared = await broker.execute(request())
        assert undeclared.fault_code == "CAPABILITY_NOT_DECLARED"

        declared = HardwareCapabilityBroker(
            declared_capabilities=frozenset({"vision"}), adapters={"vision": adapter}
        )
        expired = await declared.execute(
            request(idempotency_key="expired", monotonic_deadline=time.monotonic() - 1)
        )
        assert expired.state is CapabilityOutcomeState.TIMED_OUT
        assert expired.fault_code == "DEADLINE_EXPIRED"

    asyncio.run(run())


def test_cancellation_stops_running_adapter() -> None:
    class HangingAdapter:
        def __init__(self):
            self.cancelled = False

        async def invoke(self, request):
            try:
                await asyncio.sleep(30)
                return AdapterResponse(state=CapabilityOutcomeState.SUCCEEDED)
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    async def run():
        cancellations = CancellationRegistry()
        adapter = HangingAdapter()
        broker = HardwareCapabilityBroker(
            declared_capabilities=frozenset({"vision"}),
            adapters={"vision": adapter},
            cancellations=cancellations,
        )
        pending = asyncio.create_task(broker.execute(request()))
        await asyncio.sleep(0)
        cancellations.cancel("cancel-1")
        outcome = await pending
        assert outcome.state is CapabilityOutcomeState.CANCELLED
        assert adapter.cancelled

    asyncio.run(run())
