"""Bounded authenticated JSON transport over Windows named pipes."""

from __future__ import annotations

import json
import os
import secrets
import uuid
from enum import StrEnum
from multiprocessing.connection import Client, Connection, Listener
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .limits import MAX_IPC_MESSAGE_BYTES
from .version import CONTRACT_VERSION


class IpcFault(RuntimeError):
    pass


class IpcMessageTooLarge(IpcFault):
    pass


class MessageType(StrEnum):
    CAPABILITY_REQUEST = "capability_request"
    CAPABILITY_RESPONSE = "capability_response"
    PIPELINE_EXECUTE = "pipeline_execute"
    PIPELINE_RESULT = "pipeline_result"
    EVENT = "event"
    HEALTH = "health"
    SHUTDOWN = "shutdown"


class IpcEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["1.0"] = CONTRACT_VERSION
    message_type: MessageType
    request_id: str = Field(min_length=1)
    cycle_id: str | None = None
    request_sequence: int = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


def encode_envelope(envelope: IpcEnvelope) -> bytes:
    encoded = envelope.model_dump_json().encode("utf-8")
    if len(encoded) > MAX_IPC_MESSAGE_BYTES:
        raise IpcMessageTooLarge("IPC message exceeds 1 MB")
    return encoded


def decode_envelope(encoded: bytes) -> IpcEnvelope:
    if len(encoded) > MAX_IPC_MESSAGE_BYTES:
        raise IpcMessageTooLarge("IPC message exceeds 1 MB")
    try:
        return IpcEnvelope.model_validate_json(encoded)
    except ValueError as error:
        raise IpcFault(f"invalid IPC envelope: {error}") from error


class PipeConnection:
    def __init__(self, connection: Connection):
        self._connection = connection

    def send(self, envelope: IpcEnvelope) -> None:
        self._connection.send_bytes(encode_envelope(envelope))

    def receive(self) -> IpcEnvelope:
        try:
            encoded = self._connection.recv_bytes(MAX_IPC_MESSAGE_BYTES)
        except OSError as error:
            raise IpcMessageTooLarge("IPC message exceeds 1 MB") from error
        return decode_envelope(encoded)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "PipeConnection":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class AuthenticatedPipeServer:
    def __init__(self, *, address: str | None = None, auth_key: bytes | None = None):
        if os.name != "nt":
            raise IpcFault("Windows named pipes are required")
        self.address = address or (
            rf"\\.\pipe\inspection-platform-{uuid.uuid4().hex}"
        )
        self.auth_key = auth_key or secrets.token_bytes(32)
        if len(self.auth_key) < 32:
            raise ValueError("pipe authentication key must contain at least 32 bytes")
        self._listener = Listener(
            address=self.address,
            family="AF_PIPE",
            authkey=self.auth_key,
            backlog=1,
        )

    def accept(self) -> PipeConnection:
        return PipeConnection(self._listener.accept())

    def close(self) -> None:
        self._listener.close()

    def __enter__(self) -> "AuthenticatedPipeServer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def connect_pipe(address: str, auth_key: bytes) -> PipeConnection:
    if len(auth_key) < 32:
        raise ValueError("pipe authentication key must contain at least 32 bytes")
    return PipeConnection(Client(address=address, family="AF_PIPE", authkey=auth_key))
