"""Local identity and revocable server-side sessions."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from argon2 import PasswordHasher, profiles
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .settings import PlatformSettings


ROLES = frozenset(
    {
        "viewing",
        "operation",
        "manual_command",
        "configuration_approval",
        "pipeline_activation",
        "maintenance",
        "audit_administration",
    }
)

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS local_users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    roles_text TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    absolute_expires_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY(username) REFERENCES local_users(username)
);

CREATE TABLE IF NOT EXISTS authentication_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    outcome TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
"""


class AuthenticationFailed(ValueError):
    pass


class SessionInvalid(ValueError):
    pass


@dataclass(frozen=True)
class Identity:
    username: str
    roles: frozenset[str]

    def require(self, role: str) -> None:
        if role not in self.roles:
            raise PermissionError(f"role required: {role}")


class LocalIdentityStore:
    def __init__(
        self,
        settings: PlatformSettings,
        now: Callable[[], datetime] | None = None,
    ):
        self.settings = settings
        self._now = now or (lambda: datetime.now(UTC))
        self._password_hasher = PasswordHasher.from_parameters(
            profiles.RFC_9106_LOW_MEMORY
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.settings.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(AUTH_SCHEMA)

    def create_approver(self, username: str, password: str) -> None:
        normalized = username.strip()
        if not normalized:
            raise ValueError("username cannot be empty")
        if not password:
            raise ValueError("password cannot be empty")
        now = self._now().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO local_users(
                    username, password_hash, roles_text, enabled, created_at
                ) VALUES (?, ?, ?, 1, ?)
                """,
                (
                    normalized,
                    self._password_hasher.hash(password),
                    ",".join(sorted(ROLES)),
                    now,
                ),
            )

    def authenticate(self, username: str, password: str) -> str:
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT username, password_hash, roles_text, enabled
                FROM local_users WHERE username = ?
                """,
                (username,),
            ).fetchone()
            verified = False
            if row is not None and row["enabled"] == 1:
                try:
                    verified = self._password_hasher.verify(
                        row["password_hash"], password
                    )
                except (VerifyMismatchError, InvalidHashError):
                    verified = False
            connection.execute(
                """
                INSERT INTO authentication_events(username, outcome, occurred_at)
                VALUES (?, ?, ?)
                """,
                (username, "success" if verified else "failure", now.isoformat()),
            )
            if not verified:
                raise AuthenticationFailed("invalid username or password")

            token = secrets.token_urlsafe(32)
            token_hash = self._token_hash(token)
            connection.execute(
                """
                INSERT INTO sessions(
                    token_hash, username, created_at, last_seen_at,
                    absolute_expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    token_hash,
                    username,
                    now.isoformat(),
                    now.isoformat(),
                    (
                        now + timedelta(seconds=self.settings.session_absolute_seconds)
                    ).isoformat(),
                ),
            )
            return token

    def validate_session(self, token: str, *, touch: bool = True) -> Identity:
        now = self._now()
        token_hash = self._token_hash(token)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.username, s.last_seen_at, s.absolute_expires_at,
                       s.revoked_at, u.roles_text, u.enabled
                FROM sessions s
                JOIN local_users u ON u.username = s.username
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None or row["revoked_at"] is not None or row["enabled"] != 1:
                raise SessionInvalid("session is not active")
            last_seen = datetime.fromisoformat(row["last_seen_at"])
            absolute_expiry = datetime.fromisoformat(row["absolute_expires_at"])
            inactivity_expiry = last_seen + timedelta(
                seconds=self.settings.session_inactivity_seconds
            )
            if now >= inactivity_expiry or now >= absolute_expiry:
                connection.execute(
                    "UPDATE sessions SET revoked_at = ? WHERE token_hash = ?",
                    (now.isoformat(), token_hash),
                )
                raise SessionInvalid("session has expired")
            if touch:
                connection.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                    (now.isoformat(), token_hash),
                )
            return Identity(
                username=row["username"],
                roles=frozenset(row["roles_text"].split(",")),
            )

    def revoke_session(self, token: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ?",
                (self._now().isoformat(), self._token_hash(token)),
            )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
