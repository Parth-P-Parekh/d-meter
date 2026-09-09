import json
import zipfile

from fastapi.testclient import TestClient

from inspection_platform.api import ALLOWED_ORIGIN, CSRF_COOKIE, CSRF_HEADER
from inspection_platform.app import create_full_app
from inspection_platform.auth import LocalIdentityStore
from inspection_platform.contracts import LifecycleState
from inspection_platform.package_registry import PackageRegistry
from inspection_platform.packages import PackageArchive, approve_package
from inspection_platform.recipes import MAX_RECIPE_BYTES, approve_recipe
from inspection_platform.settings import PlatformSettings
from inspection_platform.signing import ApprovalSigner
from inspection_platform.storage import DurableResultStore
from inspection_platform.supervisor import ControlSupervisor


def make_signed_pipeline(tmp_path, signer):
    source = tmp_path / "pipeline-source.zip"
    approved = tmp_path / "pipeline-approved.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pipeline/sample.py", "class SamplePipeline: pass")
        archive.writestr(
            "config.schema.json",
            json.dumps(
                {
                    "type": "object",
                    "properties": {"threshold": {"type": "number", "maximum": 1}},
                    "required": ["threshold"],
                    "additionalProperties": False,
                }
            ),
        )
    with zipfile.ZipFile(source) as archive:
        checksum = PackageArchive.payload_checksum(archive)
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
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
            ),
        )
    approve_package(source, approved, signer, "owner")
    return approved, checksum


def make_signed_recipe(tmp_path, signer, checksum, version, threshold):
    source = tmp_path / f"recipe-{version}-source.json"
    approved = tmp_path / f"recipe-{version}-approved.json"
    source.write_text(
        json.dumps(
            {
                "contract_version": "1.0",
                "recipe_id": "part-check",
                "semantic_version": version,
                "pipeline_name": "sample-replay",
                "pipeline_version": "1.0.0",
                "pipeline_checksum": checksum,
                "configuration": {"threshold": threshold},
                "approval": None,
            }
        ),
        encoding="utf-8",
    )
    approve_recipe(source, approved, signer, "owner")
    return approved


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
    signer = ApprovalSigner(settings)
    signer.create_key()
    packages = PackageRegistry(settings)
    packages.initialize()
    pipeline, checksum = make_signed_pipeline(tmp_path, signer)
    packages.register(pipeline)
    packages.activate(
        package_name="sample-replay", semantic_version="1.0.0",
        state=LifecycleState.IDLE, actor="owner", request_id="package-activate"
    )
    supervisor = ControlSupervisor(state=LifecycleState.IDLE)
    client = TestClient(
        create_full_app(
            settings=settings,
            identities=identities,
            store=store,
            supervisor=supervisor,
            registry=packages,
        ),
        base_url="https://DEMETER",
    )
    login = client.post(
        "/api/v1/auth/login",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"username": "owner", "password": "right-password"},
    )
    assert login.status_code == 200
    headers = {
        "Origin": ALLOWED_ORIGIN,
        CSRF_HEADER: client.cookies.get(CSRF_COOKIE),
    }
    return client, headers, signer, checksum


def test_recipe_registration_activation_lookup_and_rollback(tmp_path) -> None:
    client, headers, signer, checksum = make_client(tmp_path)
    first = make_signed_recipe(tmp_path, signer, checksum, "1.0.0", 0.4)
    second = make_signed_recipe(tmp_path, signer, checksum, "1.1.0", 0.6)
    for release in (first, second):
        response = client.post(
            "/api/v1/recipes/register",
            headers={**headers, "Content-Type": "application/json"},
            content=release.read_bytes(),
        )
        assert response.status_code == 200

    first_activation = client.post(
        "/api/v1/recipes/part-check/1.0.0/activate",
        headers={**headers, "X-Request-ID": "recipe-activate-1"},
    )
    assert first_activation.status_code == 200
    second_activation = client.post(
        "/api/v1/recipes/part-check/1.1.0/activate",
        headers={**headers, "X-Request-ID": "recipe-activate-2"},
    )
    assert second_activation.status_code == 200
    assert client.get("/api/v1/recipes/active").json()["recipe"]["semantic_version"] == "1.1.0"
    rollback = client.post(
        "/api/v1/recipes/rollback",
        headers={**headers, "X-Request-ID": "recipe-rollback-1"},
    )
    assert rollback.status_code == 200
    assert rollback.json()["semantic_version"] == "1.0.0"


def test_recipe_upload_rejects_invalid_configuration_and_excess_size(tmp_path) -> None:
    client, headers, signer, checksum = make_client(tmp_path)
    invalid = make_signed_recipe(tmp_path, signer, checksum, "1.0.0", 2.0)
    response = client.post(
        "/api/v1/recipes/register",
        headers={**headers, "Content-Type": "application/json"},
        content=invalid.read_bytes(),
    )
    assert response.status_code == 422
    oversized = client.post(
        "/api/v1/recipes/register",
        headers={**headers, "Content-Type": "application/json"},
        content=b" " * (MAX_RECIPE_BYTES + 1),
    )
    assert oversized.status_code == 413
