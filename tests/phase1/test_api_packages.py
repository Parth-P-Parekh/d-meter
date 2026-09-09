import json
import zipfile

from fastapi.testclient import TestClient

from inspection_platform.api import ALLOWED_ORIGIN, CSRF_COOKIE, CSRF_HEADER
from inspection_platform.app import create_full_app
from inspection_platform.auth import LocalIdentityStore
from inspection_platform.contracts import LifecycleState
from inspection_platform.package_registry import PackageRegistry
from inspection_platform.packages import PackageArchive, approve_package
from inspection_platform.settings import PlatformSettings
from inspection_platform.signing import ApprovalSigner
from inspection_platform.storage import DurableResultStore
from inspection_platform.supervisor import ControlSupervisor


def make_package(tmp_path):
    unsigned = tmp_path / "unsigned.zip"
    approved = tmp_path / "approved.zip"
    with zipfile.ZipFile(unsigned, "w") as archive:
        archive.writestr("pipeline/sample.py", "class SamplePipeline: pass")
        archive.writestr(
            "config.schema.json", '{"type":"object","additionalProperties":false}'
        )
    with zipfile.ZipFile(unsigned, "r") as archive:
        checksum = PackageArchive.payload_checksum(archive)
    manifest = {
        "contract_version": "1.0",
        "package_format": "zip",
        "package_name": "sample-replay",
        "semantic_version": "1.0.0",
        "entrypoint": "sample:SamplePipeline",
        "code_checksum": checksum,
        "configuration_schema_version": "1.0",
        "compatible_platform_version_range": ">=0.1.0,<0.2.0",
        "required_capabilities": [],
        "dependencies": [],
        "approval": None,
    }
    with zipfile.ZipFile(unsigned, "a") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
    return unsigned, approved


def make_client(tmp_path):
    settings = PlatformSettings(
        data_root=tmp_path / "data",
        low_space_warning_bytes=1,
        stop_accepting_cycles_bytes=1,
    )
    store = DurableResultStore(settings)
    store.initialize()
    identities = LocalIdentityStore(settings)
    identities.initialize()
    identities.create_approver("owner", "right-password")
    registry = PackageRegistry(settings)
    registry.initialize()
    signer = ApprovalSigner(settings)
    signer.create_key()
    supervisor = ControlSupervisor(state=LifecycleState.IDLE)
    client = TestClient(
        create_full_app(
            settings=settings,
            identities=identities,
            store=store,
            supervisor=supervisor,
            registry=registry,
        ),
        base_url="https://DEMETER",
    )
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"username": "owner", "password": "right-password"},
    )
    assert response.status_code == 200
    headers = {
        "Origin": ALLOWED_ORIGIN,
        CSRF_HEADER: client.cookies.get(CSRF_COOKIE),
    }
    return client, headers, signer


def test_signed_package_upload_and_idle_activation(tmp_path) -> None:
    client, headers, signer = make_client(tmp_path)
    unsigned, approved = make_package(tmp_path)
    approve_package(unsigned, approved, signer, "owner")

    registration = client.post(
        "/api/v1/packages/register",
        headers={**headers, "Content-Type": "application/zip"},
        content=approved.read_bytes(),
    )
    assert registration.status_code == 200
    activation = client.post(
        "/api/v1/packages/sample-replay/1.0.0/activate",
        headers={**headers, "X-Request-ID": "activate-1"},
    )
    assert activation.status_code == 200
    assert activation.json()["semantic_version"] == "1.0.0"


def test_unsigned_package_upload_is_rejected(tmp_path) -> None:
    client, headers, _ = make_client(tmp_path)
    unsigned, _ = make_package(tmp_path)

    response = client.post(
        "/api/v1/packages/register",
        headers={**headers, "Content-Type": "application/zip"},
        content=unsigned.read_bytes(),
    )
    assert response.status_code == 422
    assert "not approved" in response.json()["detail"]
