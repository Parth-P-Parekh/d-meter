"""Authenticated restart-review and reset routes."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException

from .api import ALLOWED_ORIGIN, CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from .auth import Identity, LocalIdentityStore, SessionInvalid
from .recovery import RecoveryDecision, RecoveryFault, RecoveryReview, RecoveryReviewQueue
from .storage import InsufficientDiskSpace
from .supervisor import ControlSupervisor
from .version import API_PREFIX


def recovery_router(
    *,
    identities: LocalIdentityStore,
    queue: RecoveryReviewQueue,
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

    @router.get("/recovery-reviews")
    def list_recovery_reviews(
        identity: Identity = Depends(current_identity),
    ) -> dict[str, object]:
        identity.require("viewing")
        return {
            "reviews": [
                _review_response(review)
                for review in queue.list_reviews(outstanding_only=True)
            ]
        }

    @router.post("/recovery-reviews/{review_id}/dismiss")
    def dismiss_recovery_review(
        review_id: str,
        request_id: str = Header(alias="X-Request-ID"),
        identity: Identity = Depends(mutation_guard),
    ) -> dict[str, object]:
        identity.require("operation")
        return _decide(queue, review_id, RecoveryDecision.DISMISS, identity, request_id)

    @router.post("/recovery-reviews/{review_id}/rerun")
    def rerun_recovery_review(
        review_id: str,
        request_id: str = Header(alias="X-Request-ID"),
        identity: Identity = Depends(mutation_guard),
    ) -> dict[str, object]:
        identity.require("operation")
        return _decide(queue, review_id, RecoveryDecision.RERUN, identity, request_id)

    @router.post("/reset")
    def reset_after_recovery(
        request_id: str = Header(alias="X-Request-ID"),
        identity: Identity = Depends(mutation_guard),
    ) -> dict[str, object]:
        identity.require("operation")
        try:
            replacement = queue.reset(
                supervisor=supervisor,
                actor=identity.username,
                request_id=request_id,
            )
        except InsufficientDiskSpace as error:
            raise HTTPException(status_code=507, detail=str(error)) from error
        except RecoveryFault as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "state": supervisor.state.value,
            "replacement_cycle_id": replacement.cycle_id if replacement else None,
        }

    return router


def _decide(
    queue: RecoveryReviewQueue,
    review_id: str,
    decision: RecoveryDecision,
    identity: Identity,
    request_id: str,
) -> dict[str, object]:
    try:
        review = queue.decide(
            review_id=review_id,
            decision=decision,
            actor=identity.username,
            request_id=request_id,
        )
    except RecoveryFault as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _review_response(review)


def _review_response(review: RecoveryReview) -> dict[str, object]:
    return {
        "review_id": review.review_id,
        "original_cycle_id": review.original_cycle_id,
        "interrupted_state": review.interrupted_state.value,
        "decision": review.decision.value if review.decision else None,
        "detected_at": review.detected_at,
        "decided_at": review.decided_at,
        "decided_by": review.decided_by,
        "replacement_cycle_id": review.replacement_cycle_id,
        "reset_completed_at": review.reset_completed_at,
    }
