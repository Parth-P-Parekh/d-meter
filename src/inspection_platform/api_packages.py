"""Authenticated package registration, activation, and rollback routes."""

from __future__ import annotations

import hmac
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request

from .api import ALLOWED_ORIGIN, CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from .auth import Identity, LocalIdentityStore, SessionInvalid
from .package_registry import PackageRegistry, PackageRegistryFault
from .packages import MAX_PACKAGE_BYTES, PackageFault
from .supervisor import ControlSupervisor
from .version import API_PREFIX


def package_router(
    *,
    identities: LocalIdentityStore,
    registry: PackageRegistry,
    supervisor: ControlSupervisor,
) -> APIRouter:
    router = APIRouter(prefix=API_PREFIX)

    def current_identity(
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> Identity:
        if session_token is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            return identities.validate_session(session_token)
        except SessionInvalid as error:
            raise HTTPException(status_code=401, detail="authentication required") from error

    def mutation_guard(
        identity: Identity = Depends(current_identity),
        origin: str | None = Header(default=None),
        csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
        csrf_header: str | None = Header(default=None, alias=CSRF_HEADER),
    ) -> Identity:
        if origin != ALLOWED_ORIGIN:
            raise HTTPException(status_code=403, detail="origin is not allowed")
        if (
            csrf_cookie is None
            or csrf_header is None
            or not hmac.compare_digest(csrf_cookie, csrf_header)
        ):
            raise HTTPException(status_code=403, detail="request token is invalid")
        return identity

    @router.post("/packages/register")
    async def register_package(
        request: Request,
        identity: Identity = Depends(mutation_guard),
    ) -> dict[str, str]:
        identity.require("configuration_approval")
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/zip":
            raise HTTPException(status_code=415, detail="application/zip is required")
        registry.package_root.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        size = 0
        try:
            with tempfile.NamedTemporaryFile(
                dir=registry.package_root, suffix=".upload", delete=False
            ) as temporary:
                temporary_name = temporary.name
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_PACKAGE_BYTES:
                        raise HTTPException(status_code=413, detail="package exceeds 2 GB")
                    temporary.write(chunk)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                manifest = registry.register(Path(temporary_name))
            except (PackageFault, PackageRegistryFault) as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            return {
                "package_name": manifest.package_name,
                "semantic_version": manifest.semantic_version,
                "approved_by": manifest.approval.approver if manifest.approval else "",
            }
        finally:
            if temporary_name and Path(temporary_name).exists():
                Path(temporary_name).unlink()

    @router.post("/packages/{name}/{version}/activate")
    def activate_package(
        name: str,
        version: str,
        request_id: str = Header(alias="X-Request-ID"),
        identity: Identity = Depends(mutation_guard),
    ) -> dict[str, str]:
        identity.require("pipeline_activation")
        try:
            active = registry.activate(
                package_name=name,
                semantic_version=version,
                state=supervisor.state,
                actor=identity.username,
                request_id=request_id,
            )
        except (PackageFault, PackageRegistryFault) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "package_name": active.package_name,
            "semantic_version": active.semantic_version,
            "archive_sha256": active.archive_sha256,
        }

    @router.post("/packages/rollback")
    def rollback_package(
        request_id: str = Header(alias="X-Request-ID"),
        identity: Identity = Depends(mutation_guard),
    ) -> dict[str, str]:
        identity.require("pipeline_activation")
        try:
            active = registry.rollback(
                state=supervisor.state,
                actor=identity.username,
                request_id=request_id,
            )
        except PackageRegistryFault as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "package_name": active.package_name,
            "semantic_version": active.semantic_version,
            "archive_sha256": active.archive_sha256,
        }

    return router
