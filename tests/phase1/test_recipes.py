import json
import zipfile

import pytest

from inspection_platform.contracts import LifecycleState
from inspection_platform.package_registry import PackageRegistry
from inspection_platform.packages import PackageArchive, approve_package
from inspection_platform.recipes import RecipeFault, RecipeRegistry, approve_recipe
from inspection_platform.settings import PlatformSettings
from inspection_platform.signing import ApprovalSigner
from inspection_platform.storage import DurableResultStore


def build_pipeline(tmp_path, signer, registry):
    unsigned = tmp_path / "pipeline-unsigned.zip"
    approved = tmp_path / "pipeline-approved.zip"
    with zipfile.ZipFile(unsigned, "w") as archive:
        archive.writestr("pipeline/sample.py", "class SamplePipeline: pass")
        archive.writestr(
            "config.schema.json",
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {
                        "threshold": {"type": "number", "minimum": 0, "maximum": 1}
                    },
                    "required": ["threshold"],
                    "additionalProperties": False,
                }
            ),
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
    approve_package(unsigned, approved, signer, "owner")
    registry.register(approved)
    registry.activate(
        package_name="sample-replay",
        semantic_version="1.0.0",
        state=LifecycleState.IDLE,
        actor="owner",
        request_id="activate-package",
    )
    return checksum


def write_recipe(path, *, version, checksum, threshold):
    path.write_text(
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


def setup_registry(tmp_path):
    settings = PlatformSettings(data_root=tmp_path / "data")
    store = DurableResultStore(settings)
    store.initialize()
    signer = ApprovalSigner(settings)
    signer.create_key()
    packages = PackageRegistry(settings)
    packages.initialize()
    checksum = build_pipeline(tmp_path, signer, packages)
    recipes = RecipeRegistry(settings, signer)
    recipes.initialize()
    return signer, recipes, checksum


def approve_release(tmp_path, signer, *, version, checksum, threshold):
    source = tmp_path / f"recipe-{version}-unsigned.json"
    approved = tmp_path / f"recipe-{version}-approved.json"
    write_recipe(
        source,
        version=version,
        checksum=checksum,
        threshold=threshold,
    )
    approve_recipe(source, approved, signer, "owner")
    return approved


def test_signed_recipe_is_validated_activated_and_rolled_back(tmp_path) -> None:
    signer, recipes, checksum = setup_registry(tmp_path)
    first = approve_release(
        tmp_path, signer, version="1.0.0", checksum=checksum, threshold=0.5
    )
    second = approve_release(
        tmp_path, signer, version="1.1.0", checksum=checksum, threshold=0.7
    )
    recipes.register(first)
    recipes.register(second)
    recipes.activate(
        recipe_id="part-check", semantic_version="1.0.0",
        state=LifecycleState.IDLE, actor="owner", request_id="recipe-activate-1"
    )
    active = recipes.activate(
        recipe_id="part-check", semantic_version="1.1.0",
        state=LifecycleState.IDLE, actor="owner", request_id="recipe-activate-2"
    )
    assert active.semantic_version == "1.1.0"
    rolled_back = recipes.rollback(
        state=LifecycleState.IDLE, actor="owner", request_id="recipe-rollback-1"
    )
    assert rolled_back.semantic_version == "1.0.0"


def test_invalid_configuration_and_untrusted_signer_are_rejected(tmp_path) -> None:
    signer, recipes, checksum = setup_registry(tmp_path)
    invalid = approve_release(
        tmp_path, signer, version="1.0.0", checksum=checksum, threshold=2.0
    )
    with pytest.raises(RecipeFault, match="configuration is invalid"):
        recipes.register(invalid)

    attacker_settings = PlatformSettings(data_root=tmp_path / "attacker")
    attacker = ApprovalSigner(attacker_settings)
    attacker.create_key()
    untrusted = approve_release(
        tmp_path, attacker, version="2.0.0", checksum=checksum, threshold=0.5
    )
    with pytest.raises(RecipeFault, match="configured approver key"):
        recipes.register(untrusted)


def test_recipe_activation_requires_idle_and_unique_request_id(tmp_path) -> None:
    signer, recipes, checksum = setup_registry(tmp_path)
    approved = approve_release(
        tmp_path, signer, version="1.0.0", checksum=checksum, threshold=0.5
    )
    recipes.register(approved)
    with pytest.raises(RecipeFault, match="only in idle"):
        recipes.activate(
            recipe_id="part-check", semantic_version="1.0.0",
            state=LifecycleState.READY, actor="owner", request_id="recipe-activate"
        )
    recipes.activate(
        recipe_id="part-check", semantic_version="1.0.0",
        state=LifecycleState.IDLE, actor="owner", request_id="recipe-activate"
    )
    with pytest.raises(RecipeFault, match="already been used"):
        recipes.activate(
            recipe_id="part-check", semantic_version="1.0.0",
            state=LifecycleState.IDLE, actor="owner", request_id="recipe-activate"
        )
