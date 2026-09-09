"""Ed25519 approval signatures with a Windows-protected private key."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import tempfile
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .settings import PlatformSettings


class SigningFault(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob_from_bytes(value: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(value)
    blob = _DataBlob(
        len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    )
    return blob, buffer


def protect_for_current_windows_user(value: bytes) -> bytes:
    if os.name != "nt":
        raise SigningFault("Windows data protection is required")
    source, source_buffer = _blob_from_bytes(value)
    destination = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(destination)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(destination.pbData)
        del source_buffer


def unprotect_for_current_windows_user(value: bytes) -> bytes:
    if os.name != "nt":
        raise SigningFault("Windows data protection is required")
    source, source_buffer = _blob_from_bytes(value)
    destination = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(destination)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(destination.pbData)
        del source_buffer


def canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class ApprovalSignature:
    algorithm: str
    approver: str
    payload_sha256: str
    public_key_base64: str
    signature_base64: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class ApprovalSigner:
    def __init__(self, settings: PlatformSettings):
        self.key_directory = settings.data_root / "signing"
        self.private_key_path = self.key_directory / "approver-ed25519.private.dpapi"
        self.public_key_path = self.key_directory / "approver-ed25519.public"

    def create_key(self) -> None:
        self.key_directory.mkdir(parents=True, exist_ok=True)
        if self.private_key_path.exists() or self.public_key_path.exists():
            raise SigningFault("signing key already exists")
        private_key = Ed25519PrivateKey.generate()
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self._durable_write(
            self.private_key_path, protect_for_current_windows_user(private_bytes)
        )
        self._durable_write(self.public_key_path, public_bytes)

    def sign(self, *, approver: str, payload: dict[str, Any]) -> ApprovalSignature:
        if not approver.strip():
            raise ValueError("approver cannot be empty")
        private_key = self._load_private_key()
        payload_bytes = canonical_json_bytes(payload)
        digest = hashlib.sha256(payload_bytes).digest()
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return ApprovalSignature(
            algorithm="Ed25519-SHA256",
            approver=approver,
            payload_sha256=digest.hex(),
            public_key_base64=base64.b64encode(public_bytes).decode("ascii"),
            signature_base64=base64.b64encode(private_key.sign(digest)).decode("ascii"),
        )

    @staticmethod
    def verify(payload: dict[str, Any], approval: ApprovalSignature) -> None:
        if approval.algorithm != "Ed25519-SHA256":
            raise SigningFault("unsupported signature algorithm")
        digest = hashlib.sha256(canonical_json_bytes(payload)).digest()
        if digest.hex() != approval.payload_sha256:
            raise SigningFault("approved payload fingerprint does not match")
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(approval.public_key_base64, validate=True)
        )
        try:
            public_key.verify(
                base64.b64decode(approval.signature_base64, validate=True), digest
            )
        except ValueError as error:
            raise SigningFault("approval signature is malformed") from error
        except Exception as error:
            raise SigningFault("approval signature is invalid") from error

    def _load_private_key(self) -> Ed25519PrivateKey:
        if not self.private_key_path.exists():
            raise SigningFault("signing key does not exist")
        raw = unprotect_for_current_windows_user(self.private_key_path.read_bytes())
        return Ed25519PrivateKey.from_private_bytes(raw)

    @staticmethod
    def _durable_write(path: Path, content: bytes) -> None:
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.replace(temporary_name, path)
        finally:
            if temporary_name and Path(temporary_name).exists():
                Path(temporary_name).unlink()
