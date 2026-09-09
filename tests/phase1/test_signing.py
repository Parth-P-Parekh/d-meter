import pytest

from inspection_platform.settings import PlatformSettings
from inspection_platform.signing import ApprovalSigner, SigningFault


def test_signature_accepts_exact_payload_and_rejects_changes(tmp_path) -> None:
    signer = ApprovalSigner(PlatformSettings(data_root=tmp_path))
    signer.create_key()
    payload = {"package": "sample", "version": "1.0.0", "checksum": "abc"}
    approval = signer.sign(approver="owner", payload=payload)

    signer.verify(payload, approval)
    with pytest.raises(SigningFault, match="does not match"):
        signer.verify({**payload, "checksum": "changed"}, approval)


def test_private_key_is_not_stored_as_raw_key(tmp_path) -> None:
    signer = ApprovalSigner(PlatformSettings(data_root=tmp_path))
    signer.create_key()

    assert signer.private_key_path.read_bytes() != signer._load_private_key().private_bytes_raw()


def test_key_creation_cannot_overwrite_existing_identity(tmp_path) -> None:
    signer = ApprovalSigner(PlatformSettings(data_root=tmp_path))
    signer.create_key()

    with pytest.raises(SigningFault, match="already exists"):
        signer.create_key()
