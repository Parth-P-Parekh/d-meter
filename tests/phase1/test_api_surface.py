from fastapi.testclient import TestClient

from inspection_platform.api import create_app
from inspection_platform.auth import LocalIdentityStore
from inspection_platform.contracts import LifecycleState
from inspection_platform.settings import PlatformSettings
from inspection_platform.storage import DurableResultStore
from inspection_platform.supervisor import ControlSupervisor


def test_documentation_and_schema_routes_are_disabled(tmp_path) -> None:
    settings = PlatformSettings(data_root=tmp_path)
    store = DurableResultStore(settings)
    store.initialize()
    identities = LocalIdentityStore(settings)
    identities.initialize()
    app = create_app(
        settings=settings,
        identities=identities,
        store=store,
        supervisor=ControlSupervisor(state=LifecycleState.IDLE),
    )
    client = TestClient(app, base_url="https://DEMETER")

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/api/v1/openapi.json").status_code == 404
