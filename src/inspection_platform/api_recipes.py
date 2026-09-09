"""Authenticated signed-recipe registration and activation routes."""

from __future__ import annotations

import hmac
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request

from .api import ALLOWED_ORIGIN, CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from .auth import Identity, LocalIdentityStore, SessionInvalid
from .recipes import MAX_RECIPE_BYTES, RecipeFault, RecipeRegistry
from .supervisor import ControlSupervisor
from .version import API_PREFIX


def recipe_router(
    *,
    identities: LocalIdentityStore,
    registry: RecipeRegistry,
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

    @router.get("/recipes/active")
    def active_recipe(
        identity: Identity = Depends(current_identity),
    ) -> dict[str, object]:
        identity.require("viewing")
        active = registry.current()
        return {"recipe": _active_response(active) if active else None}

    @router.post("/recipes/register")
    async def register_recipe(
        request: Request,
        identity: Identity = Depends(mutation_guard),
    ) -> dict[str, str]:
        identity.require("configuration_approval")
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            raise HTTPException(status_code=415, detail="application/json is required")
        registry.recipe_root.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        size = 0
        try:
            with tempfile.NamedTemporaryFile(
                dir=registry.recipe_root, suffix=".upload", delete=False
            ) as temporary:
                temporary_name = temporary.name
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_RECIPE_BYTES:
                        raise HTTPException(status_code=413, detail="recipe exceeds 512 KB")
                    temporary.write(chunk)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                release = registry.register(Path(temporary_name))
            except RecipeFault as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            return {
                "recipe_id": release.recipe_id,
                "semantic_version": release.semantic_version,
                "pipeline_name": release.pipeline_name,
                "pipeline_version": release.pipeline_version,
                "approved_by": release.approval.approver if release.approval else "",
            }
        finally:
            if temporary_name and Path(temporary_name).exists():
                Path(temporary_name).unlink()

    @router.post("/recipes/{recipe_id}/{version}/activate")
    def activate_recipe(
        recipe_id: str,
        version: str,
        request_id: str = Header(alias="X-Request-ID"),
        identity: Identity = Depends(mutation_guard),
    ) -> dict[str, str]:
        identity.require("configuration_approval")
        try:
            active = registry.activate(
                recipe_id=recipe_id,
                semantic_version=version,
                state=supervisor.state,
                actor=identity.username,
                request_id=request_id,
            )
        except RecipeFault as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _active_response(active)

    @router.post("/recipes/rollback")
    def rollback_recipe(
        request_id: str = Header(alias="X-Request-ID"),
        identity: Identity = Depends(mutation_guard),
    ) -> dict[str, str]:
        identity.require("configuration_approval")
        try:
            active = registry.rollback(
                state=supervisor.state,
                actor=identity.username,
                request_id=request_id,
            )
        except RecipeFault as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _active_response(active)

    return router


def _active_response(active) -> dict[str, str]:
    return {
        "recipe_id": active.recipe_id,
        "semantic_version": active.semantic_version,
        "release_sha256": active.release_sha256,
        "pipeline_name": active.pipeline_name,
        "pipeline_version": active.pipeline_version,
        "pipeline_checksum": active.pipeline_checksum,
    }
