"""Authenticated Phase 1 API boundary."""

from __future__ import annotations

import hmac
import secrets

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .auth import (
    AuthenticationFailed,
    Identity,
    LocalIdentityStore,
    SessionInvalid,
)
from .settings import PlatformSettings
from .storage import DurableResultStore
from .supervisor import ControlSupervisor
from .version import API_PREFIX, CONTRACT_VERSION, PLATFORM_VERSION


ALLOWED_ORIGIN = "https://DEMETER"
SESSION_COOKIE = "inspection_session"
CSRF_COOKIE = "inspection_csrf"
CSRF_HEADER = "X-Inspection-CSRF"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(ApiModel):
    username: str = Field(min_length=1)
    password: SecretStr


class SessionResponse(ApiModel):
    username: str
    roles: tuple[str, ...]


def create_app(
    *,
    settings: PlatformSettings,
    identities: LocalIdentityStore,
    store: DurableResultStore,
    supervisor: ControlSupervisor,
) -> FastAPI:
    app = FastAPI(
        title="Inspection Platform",
        version=PLATFORM_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def require_origin(origin: str | None = Header(default=None)) -> None:
        if origin != ALLOWED_ORIGIN:
            raise HTTPException(status_code=403, detail="origin is not allowed")

    def current_identity(
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> Identity:
        if session_token is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            return identities.validate_session(session_token)
        except SessionInvalid as error:
            raise HTTPException(status_code=401, detail="authentication required") from error

    def require_csrf(
        _: None = Depends(require_origin),
        csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
        csrf_header: str | None = Header(default=None, alias=CSRF_HEADER),
    ) -> None:
        if (
            csrf_cookie is None
            or csrf_header is None
            or not hmac.compare_digest(csrf_cookie, csrf_header)
        ):
            raise HTTPException(status_code=403, detail="request token is invalid")

    @app.post(f"{API_PREFIX}/auth/login", response_model=SessionResponse)
    def login(
        body: LoginRequest,
        response: Response,
        _: None = Depends(require_origin),
    ) -> SessionResponse:
        try:
            session_token = identities.authenticate(
                body.username, body.password.get_secret_value()
            )
            identity = identities.validate_session(session_token, touch=False)
        except AuthenticationFailed as error:
            raise HTTPException(status_code=401, detail="invalid username or password") from error
        csrf_token = secrets.token_urlsafe(32)
        response.set_cookie(
            SESSION_COOKIE,
            session_token,
            max_age=settings.session_absolute_seconds,
            secure=True,
            httponly=True,
            samesite="strict",
            path=API_PREFIX,
        )
        response.set_cookie(
            CSRF_COOKIE,
            csrf_token,
            max_age=settings.session_absolute_seconds,
            secure=True,
            httponly=False,
            samesite="strict",
            path=API_PREFIX,
        )
        return SessionResponse(
            username=identity.username, roles=tuple(sorted(identity.roles))
        )

    @app.post(f"{API_PREFIX}/auth/logout", status_code=204)
    def logout(
        response: Response,
        identity: Identity = Depends(current_identity),
        _: None = Depends(require_csrf),
        session_token: str = Cookie(alias=SESSION_COOKIE),
    ) -> Response:
        del identity
        identities.revoke_session(session_token)
        response.delete_cookie(SESSION_COOKIE, path=API_PREFIX, secure=True, httponly=True)
        response.delete_cookie(CSRF_COOKIE, path=API_PREFIX, secure=True)
        response.status_code = 204
        return response

    @app.get(f"{API_PREFIX}/health")
    def health(_: Identity = Depends(current_identity)) -> dict[str, object]:
        disk = store.disk_health()
        return {
            "status": "healthy" if disk.accepts_new_cycles else "held",
            "platform_version": PLATFORM_VERSION,
            "contract_version": CONTRACT_VERSION,
            "disk": {
                "free_bytes": disk.free_bytes,
                "warning": disk.warning,
                "accepts_new_cycles": disk.accepts_new_cycles,
            },
        }

    @app.get(f"{API_PREFIX}/state")
    def state(_: Identity = Depends(current_identity)) -> dict[str, str]:
        return {"state": supervisor.state.value}

    return app
