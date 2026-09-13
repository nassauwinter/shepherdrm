"""Verify managed resource-secret authenticated encryption behavior."""

from __future__ import annotations

import base64
import json
import uuid

import pytest

from shepherd_rm.config import Settings
from shepherd_rm.resource_secrets.crypto import (
    SecretCipher,
    SecretDecryptionError,
    SecretEncryptionUnavailable,
)

KEY = base64.b64encode(b"k" * 32).decode()


def cipher() -> SecretCipher:
    """Create a cipher with one deterministic test key and random nonces."""
    return SecretCipher(
        Settings(
            secret_encryption_active_key_id="test-key",
            secret_encryption_keys={"test-key": KEY},
        )
    )


def test_managed_secret_round_trip_uses_random_authenticated_envelopes() -> None:
    """The same value encrypts differently and decrypts only in its bound context."""
    resource_id = uuid.uuid4()
    secret_id = uuid.uuid4()
    first = cipher().encrypt("credential", resource_id, secret_id)
    second = cipher().encrypt("credential", resource_id, secret_id)

    assert first.envelope != second.envelope
    assert b"credential" not in first.envelope
    assert cipher().decrypt(first.envelope, first.key_id, resource_id, secret_id) == "credential"
    with pytest.raises(SecretDecryptionError):
        cipher().decrypt(first.envelope, first.key_id, uuid.uuid4(), secret_id)


def test_managed_secret_rejects_tampered_ciphertext() -> None:
    """Authenticated decryption rejects a modified encrypted payload without plaintext output."""
    resource_id = uuid.uuid4()
    secret_id = uuid.uuid4()
    encrypted = cipher().encrypt("credential", resource_id, secret_id)
    payload = json.loads(encrypted.envelope)
    payload["ciphertext"] = base64.b64encode(b"tampered").decode()

    with pytest.raises(SecretDecryptionError, match="could not be decrypted"):
        cipher().decrypt(json.dumps(payload).encode(), encrypted.key_id, resource_id, secret_id)


def test_managed_secret_write_requires_an_active_configured_key() -> None:
    """A deployment without an active key cannot accept managed secret material."""
    unavailable = SecretCipher(Settings())

    with pytest.raises(SecretEncryptionUnavailable, match="not configured"):
        unavailable.encrypt("credential", uuid.uuid4(), uuid.uuid4())


def test_managed_secret_read_requires_its_historical_key() -> None:
    """Removing a historical key makes records encrypted by it unavailable."""
    resource_id = uuid.uuid4()
    secret_id = uuid.uuid4()
    encrypted = cipher().encrypt("credential", resource_id, secret_id)

    with pytest.raises(SecretEncryptionUnavailable, match="required"):
        SecretCipher(Settings()).decrypt(
            encrypted.envelope, encrypted.key_id, resource_id, secret_id
        )
