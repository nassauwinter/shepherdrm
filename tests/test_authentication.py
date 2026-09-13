"""Verify standalone authentication primitives and identity input validation."""

import pytest
from pydantic import ValidationError

from shepherd_rm.identity.authentication import hash_password, token_hash, verify_password
from shepherd_rm.identity.models import TokenCreate


def test_password_hash_does_not_store_plaintext_and_verifies() -> None:
    """Argon2id hashes verify the password without retaining its plaintext value."""
    password = "a-secure-test-password"
    password_hash = hash_password(password)

    assert password not in password_hash
    assert password_hash.startswith("$argon2id$")
    assert verify_password(password_hash, password)
    assert not verify_password(password_hash, "incorrect-password")


def test_token_hash_is_deterministic_without_exposing_token() -> None:
    """Bearer tokens have stable lookup digests that do not contain the token value."""
    token = "srm_example-token-value"

    assert token_hash(token) == token_hash(token)
    assert token not in token_hash(token)


def test_token_expiration_requires_a_timezone() -> None:
    """Token creation rejects an expiration timestamp with ambiguous timezone semantics."""
    with pytest.raises(ValidationError, match="must include a timezone offset"):
        TokenCreate(name="ambiguous", expires_at="2030-01-01T00:00:00")
