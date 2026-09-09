"""Composition root for the Phase 1 HTTP application."""

from fastapi import FastAPI

from .api import create_app
from .api_packages import package_router
from .api_recipes import recipe_router
from .api_recovery import recovery_router
from .auth import LocalIdentityStore
from .contracts import LifecycleState
from .package_registry import PackageRegistry
from .recipes import RecipeRegistry
from .recovery import RecoveryReviewQueue
from .settings import PlatformSettings
from .signing import ApprovalSigner
from .storage import DurableResultStore
from .supervisor import ControlSupervisor
from .trusted_packages import TrustedPackageRegistry


def create_full_app(
    *,
    settings: PlatformSettings,
    identities: LocalIdentityStore,
    store: DurableResultStore,
    supervisor: ControlSupervisor,
    registry: PackageRegistry,
) -> FastAPI:
    recovery_queue = RecoveryReviewQueue(store)
    recovery_queue.initialize()
    if supervisor.state is LifecycleState.STARTUP_RECONCILIATION:
        recovery_queue.discover_after_restart(supervisor)
    signer = ApprovalSigner(settings)
    recipe_registry = RecipeRegistry(settings, signer)
    recipe_registry.initialize()
    app = create_app(
        settings=settings,
        identities=identities,
        store=store,
        supervisor=supervisor,
    )
    trusted_registry = TrustedPackageRegistry(registry, signer)
    app.include_router(
        package_router(
            identities=identities,
            registry=trusted_registry,
            supervisor=supervisor,
        )
    )
    app.include_router(
        recipe_router(
            identities=identities,
            registry=recipe_registry,
            supervisor=supervisor,
        )
    )
    app.include_router(
        recovery_router(
            identities=identities,
            queue=recovery_queue,
            supervisor=supervisor,
        )
    )
    return app
