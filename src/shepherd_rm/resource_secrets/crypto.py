"""Encrypt and decrypt managed resource-secret values with deployment keys."""

from __future__ import annotations

import base64
import binascii
import json
import os
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from shepherd_rm.config import Settings


class SecretEncryptionUnavailable(RuntimeError):
    """Indicate that managed-secret encryption is not correctly configured."""


class SecretDecryptionError(RuntimeError):
    """Indicate that stored managed-secret material cannot be safely decrypted."""


@dataclass(frozen=True)
class EncryptedSecret:
    """Carry a versioned encrypted envelope and the key that produced it."""

    envelope: bytes
    key_id: str


def _associated_data(resource_id: uuid.UUID, secret_id: uuid.UUID) -> bytes:
    return f"shepherd-rm:resource-secret:{resource_id}:{secret_id}".encode()


class SecretCipher:
    """Use an active AES-256-GCM key while retaining old keys for decryption."""

    def __init__(self, settings: Settings) -> None:
        self.active_key_id = settings.secret_encryption_active_key_id
        self.keys = {
            key_id: self._decode_key(value.get_secret_value())
            for key_id, value in settings.secret_encryption_keys.items()
        }

    @staticmethod
    def _decode_key(encoded: str) -> bytes:
        try:
            key = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise SecretEncryptionUnavailable("A secret encryption key is invalid") from error
        if len(key) != 32:
            raise SecretEncryptionUnavailable("Secret encryption keys must contain 32 bytes")
        return key

    def encrypt(self, value: str, resource_id: uuid.UUID, secret_id: uuid.UUID) -> EncryptedSecret:
        """Encrypt a UTF-8 value with a fresh nonce and resource-bound context."""
        if self.active_key_id is None or self.active_key_id not in self.keys:
            raise SecretEncryptionUnavailable("Managed-secret encryption is not configured")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.keys[self.active_key_id]).encrypt(
            nonce, value.encode(), _associated_data(resource_id, secret_id)
        )
        envelope = json.dumps(
            {
                "version": 1,
                "algorithm": "AES-256-GCM",
                "nonce": base64.b64encode(nonce).decode(),
                "ciphertext": base64.b64encode(ciphertext).decode(),
            },
            separators=(",", ":"),
        ).encode()
        return EncryptedSecret(envelope=envelope, key_id=self.active_key_id)

    def decrypt(
        self,
        envelope: bytes,
        key_id: str,
        resource_id: uuid.UUID,
        secret_id: uuid.UUID,
    ) -> str:
        """Decrypt and authenticate one stored versioned envelope."""
        key = self.keys.get(key_id)
        if key is None:
            raise SecretEncryptionUnavailable("The required secret encryption key is unavailable")
        try:
            payload = json.loads(envelope)
            if payload.get("version") != 1 or payload.get("algorithm") != "AES-256-GCM":
                raise ValueError
            nonce = base64.b64decode(payload["nonce"], validate=True)
            ciphertext = base64.b64decode(payload["ciphertext"], validate=True)
            plaintext = AESGCM(key).decrypt(
                nonce, ciphertext, _associated_data(resource_id, secret_id)
            )
            return plaintext.decode()
        except (
            InvalidTag,
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            binascii.Error,
        ) as error:
            raise SecretDecryptionError("Managed secret could not be decrypted") from error
