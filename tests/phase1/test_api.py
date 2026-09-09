from fastapi.testclient import TestClient

from inspection_platform.api import ALLOWED_ORIGIN, CSRF_COOKIE, CSRF_HEADER, create_app
from inspection_platform.auth import LocalIdentityStore
from inspection_platform.contracts import LifecycleState
from inspection_platform.settings import PlatformSettings
from inspection_platform.storage import DurableResultStore
from inspection_platform.supervisor import ControlSupervisor


def make_client(tmp_path) -> TestClient:
    settings = PlatformSettings(
        data_root=tmp_path,
        low_space_warning_bytes=1,
        stop_accepting_cycles_bytes=1,
    )
    store = DurableResultStore(settings)
    store.initialize()
    identities = LocalIdentityStore(settings)
    identities.initialize()
    identities.create_approver("owner", "right-password")
    supervisor = ControlSupervisor(state=LifecycleState.IDLE)
    app = create_app(
        settings=settings,
        identities=identities,
        store=store,
        supervisor=supervisor,
    )
    return TestClient(app, base_url="https://DEMETER")


def login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"username": "owner", "password": "right-password"},
    )
    assert response.status_code == 200


def test_every_read_requires_a_live_session(tmp_path) -> None:
    client = make_client(tmp_path)
    assert client.get("/api/v1/health").status_code == 401
    login(client)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["platform_version"] == "0.1.0"


def test_login_rejects_wrong_origin_and_wrong_password(tmp_path) -> None:
    client = make_client(tmp_path)
    wrong_origin = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://not-demeter"},
        json={"username": "owner", "password": "right-password"},
    )
    assert wrong_origin.status_code == 403
    wrong_password = client.post(
        "/api/v1/auth/login",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"username": "owner", "password": "wrong"},
    )
    assert wrong_password.status_code == 401


def test_logout_requires_origin_and_matching_request_token(tmp_path) -> None:
    client = make_client(tmp_path)
    login(client)
    csrf = client.cookies.get(CSRF_COOKIE)
    assert csrf is not None

    assert client.post("/api/v1/auth/logout").status_code == 403
    assert (
        client.post(
            "/api/v1/auth/logout",
            headers={"Origin": ALLOWED_ORIGIN, CSRF_HEADER: "wrong"},
        ).status_code
        == 403
    )
    response = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": ALLOWED_ORIGIN, CSRF_HEADER: csrf},
    )
    assert response.status_code == 204
    assert client.get("/api/v1/state").status_code == 401
