from datetime import UTC, datetime, timedelta

import pytest

from inspection_platform.auth import (
    ROLES,
    AuthenticationFailed,
    LocalIdentityStore,
    SessionInvalid,
)
from inspection_platform.settings import PlatformSettings
from inspection_platform.storage import DurableResultStore


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def make_store(tmp_path, clock: Clock) -> LocalIdentityStore:
    settings = PlatformSettings(
        data_root=tmp_path,
        session_inactivity_seconds=900,
        session_absolute_seconds=28_800,
    )
    DurableResultStore(settings).initialize()
    identities = LocalIdentityStore(settings, now=clock.now)
    identities.initialize()
    return identities


def test_password_is_hashed_and_session_has_all_approved_roles(tmp_path) -> None:
    clock = Clock()
    identities = make_store(tmp_path, clock)
    identities.create_approver("owner", "correct horse battery staple")
    token = identities.authenticate("owner", "correct horse battery staple")

    identity = identities.validate_session(token)
    assert identity.username == "owner"
    assert identity.roles == ROLES
    assert "correct horse battery staple" not in identities.settings.database_path.read_bytes().decode(
        "utf-8", errors="ignore"
    )


def test_wrong_password_fails_without_session(tmp_path) -> None:
    clock = Clock()
    identities = make_store(tmp_path, clock)
    identities.create_approver("owner", "right-password")

    with pytest.raises(AuthenticationFailed):
        identities.authenticate("owner", "wrong-password")


def test_inactivity_expiry_is_enforced(tmp_path) -> None:
    clock = Clock()
    identities = make_store(tmp_path, clock)
    identities.create_approver("owner", "right-password")
    token = identities.authenticate("owner", "right-password")
    clock.advance(901)

    with pytest.raises(SessionInvalid, match="expired"):
        identities.validate_session(token)


def test_absolute_expiry_cannot_be_extended_by_activity(tmp_path) -> None:
    clock = Clock()
    identities = make_store(tmp_path, clock)
    identities.create_approver("owner", "right-password")
    token = identities.authenticate("owner", "right-password")
    for _ in range(31):
        clock.advance(899)
        identities.validate_session(token)
    clock.advance(1_000)

    with pytest.raises(SessionInvalid, match="expired"):
        identities.validate_session(token)
