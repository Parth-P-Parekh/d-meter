from datetime import UTC, datetime

from fastapi.testclient import TestClient

from inspection_platform.api import ALLOWED_ORIGIN, CSRF_COOKIE, CSRF_HEADER
from inspection_platform.app import create_full_app
from inspection_platform.auth import LocalIdentityStore
from inspection_platform.contracts import CycleContext, LifecycleState
from inspection_platform.package_registry import PackageRegistry
from inspection_platform.settings import PlatformSettings
from inspection_platform.storage import DurableResultStore
from inspection_platform.supervisor import ControlSupervisor


def make_recovery_client(tmp_path):
    settings = PlatformSettings(
        data_root=tmp_path,
        low_space_warning_bytes=1,
        stop_accepting_cycles_bytes=1,
    )
    store = DurableResultStore(settings)
    store.initialize()
    context = CycleContext(
        cycle_id="interrupted",
        part_id="part-1",
        recipe_id="recipe-1",
        recipe_version="1.0.0",
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
    identities = LocalIdentityStore(settings)
    identities.initialize()
    identities.create_approver("owner", "right-password")
    registry = PackageRegistry(settings)
    registry.initialize()
    supervisor = ControlSupervisor()
    app = create_full_app(
        settings=settings,
        identities=identities,
        store=store,
        supervisor=supervisor,
        registry=registry,
    )
    return TestClient(app, base_url="https://DEMETER"), supervisor


def login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"username": "owner", "password": "right-password"},
    )
    assert response.status_code == 200
    return {
        "Origin": ALLOWED_ORIGIN,
        CSRF_HEADER: client.cookies.get(CSRF_COOKIE),
    }


def test_recovery_review_rerun_and_reset_routes(tmp_path) -> None:
    client, supervisor = make_recovery_client(tmp_path)
    assert supervisor.state is LifecycleState.LATCHED_FAULT
    assert client.get("/api/v1/recovery-reviews").status_code == 401
    headers = login(client)

    listed = client.get("/api/v1/recovery-reviews")
    assert listed.status_code == 200
    review = listed.json()["reviews"][0]
    assert review["original_cycle_id"] == "interrupted"

    no_token = client.post(
        f"/api/v1/recovery-reviews/{review['review_id']}/rerun",
        headers={"Origin": ALLOWED_ORIGIN, "X-Request-ID": "rerun-no-token"},
    )
    assert no_token.status_code == 403
    rerun = client.post(
        f"/api/v1/recovery-reviews/{review['review_id']}/rerun",
        headers={**headers, "X-Request-ID": "rerun-1"},
    )
    assert rerun.status_code == 200
    assert rerun.json()["decision"] == "rerun"
    assert supervisor.state is LifecycleState.LATCHED_FAULT

    repeated = client.post(
        f"/api/v1/recovery-reviews/{review['review_id']}/dismiss",
        headers={**headers, "X-Request-ID": "dismiss-too-late"},
    )
    assert repeated.status_code == 409
    reset = client.post(
        "/api/v1/reset",
        headers={**headers, "X-Request-ID": "reset-1"},
    )
    assert reset.status_code == 200
    assert reset.json()["state"] == LifecycleState.CYCLE_ACCEPTED.value
    assert reset.json()["replacement_cycle_id"] != "interrupted"
    assert client.get("/api/v1/recovery-reviews").json()["reviews"] == []
