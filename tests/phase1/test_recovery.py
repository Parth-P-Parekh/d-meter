from datetime import UTC, datetime

from inspection_platform.contracts import CycleContext, LifecycleState
from inspection_platform.recovery import RecoveryDecision, RecoveryReviewQueue
from inspection_platform.settings import PlatformSettings
from inspection_platform.storage import DurableResultStore
from inspection_platform.supervisor import ControlSupervisor


def make_store(tmp_path) -> DurableResultStore:
    store = DurableResultStore(
        PlatformSettings(
            data_root=tmp_path,
            low_space_warning_bytes=1,
            stop_accepting_cycles_bytes=1,
        )
    )
    store.initialize()
    return store


def make_interrupted_cycle(store: DurableResultStore) -> CycleContext:
    context = CycleContext(
        cycle_id="interrupted-cycle",
        part_id="part-7",
        barcode_identity="barcode-7",
        recipe_id="recipe-a",
        recipe_version="3.2.1",
        pipeline_name="sample-replay",
        pipeline_version="1.0.0",
        pipeline_checksum="sha256:approved",
        initiator_identity="owner",
        accepted_at=datetime.now(UTC),
        monotonic_deadline=100.0,
        clock_synchronized=True,
    )
    store.accept_cycle(context)
    store.record_transition(
        context.cycle_id,
        LifecycleState.CYCLE_ACCEPTED,
        LifecycleState.PIPELINE_EXECUTING,
        "WORKER_STARTED",
    )
    return context


def test_restart_latches_until_review_is_dismissed_and_reset(tmp_path) -> None:
    store = make_store(tmp_path)
    make_interrupted_cycle(store)
    queue = RecoveryReviewQueue(store)
    queue.initialize()
    supervisor = ControlSupervisor()

    reviews = queue.discover_after_restart(supervisor)
    assert supervisor.state is LifecycleState.LATCHED_FAULT
    assert len(reviews) == 1
    assert reviews[0].interrupted_state is LifecycleState.PIPELINE_EXECUTING

    queue.decide(
        review_id=reviews[0].review_id,
        decision=RecoveryDecision.DISMISS,
        actor="owner",
        request_id="dismiss-1",
    )
    assert supervisor.state is LifecycleState.LATCHED_FAULT
    assert queue.reset(
        supervisor=supervisor, actor="owner", request_id="reset-1"
    ) is None
    assert supervisor.state is LifecycleState.IDLE

    restarted = ControlSupervisor()
    assert queue.discover_after_restart(restarted) == ()
    assert restarted.state is LifecycleState.IDLE


def test_rerun_is_new_linked_cycle_created_only_during_reset(tmp_path) -> None:
    store = make_store(tmp_path)
    original = make_interrupted_cycle(store)
    queue = RecoveryReviewQueue(store)
    queue.initialize()
    supervisor = ControlSupervisor()
    review = queue.discover_after_restart(supervisor)[0]

    decided = queue.decide(
        review_id=review.review_id,
        decision=RecoveryDecision.RERUN,
        actor="owner",
        request_id="rerun-1",
    )
    assert decided.replacement_cycle_id is None
    assert supervisor.state is LifecycleState.LATCHED_FAULT

    replacement = queue.reset(
        supervisor=supervisor, actor="owner", request_id="reset-1"
    )
    assert replacement is not None
    assert replacement.cycle_id != original.cycle_id
    assert replacement.part_id == original.part_id
    assert replacement.recipe_id == original.recipe_id
    assert replacement.pipeline_checksum == original.pipeline_checksum
    assert replacement.initiator_identity == "owner"
    assert supervisor.state is LifecycleState.CYCLE_ACCEPTED

    stored = store.read_cycle(replacement.cycle_id)
    assert stored is not None
    assert stored["lifecycle_state"] == LifecycleState.CYCLE_ACCEPTED.value
    completed_review = queue.list_reviews()[0]
    assert completed_review.replacement_cycle_id == replacement.cycle_id
    assert completed_review.reset_completed_at is not None


def test_restart_discovery_is_idempotent(tmp_path) -> None:
    store = make_store(tmp_path)
    make_interrupted_cycle(store)
    queue = RecoveryReviewQueue(store)
    queue.initialize()

    first = queue.discover_after_restart(ControlSupervisor())
    second = queue.discover_after_restart(ControlSupervisor())
    assert len(first) == 1
    assert second == first
