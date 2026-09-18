"""Verify environment and file-backed runtime configuration."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from shepherd_rm.config import Settings


def write_secret(path: Path, value: str) -> Path:
    """Write one test-only secret file."""
    path.write_text(value, encoding="utf-8")
    return path


def test_database_password_file_completes_passwordless_url(tmp_path: Path) -> None:
    """A mounted password file supplies an encoded database URL password."""
    settings = Settings(
        database_url="postgresql://shepherd@postgres:5432/shepherd",
        database_password_file=write_secret(tmp_path / "password", "p@ss:/word"),
    )

    assert settings.database_url == "postgresql://shepherd:p%40ss%3A%2Fword@postgres:5432/shepherd"
    assert "database_password_file" not in settings.model_dump()


def test_database_password_file_replaces_development_default(tmp_path: Path) -> None:
    """A file alone replaces the password in the default development URL."""
    settings = Settings(database_password_file=write_secret(tmp_path / "password", "file"))

    assert settings.database_url == "postgresql://shepherd_rm:file@localhost:5432/shepherd_rm"


def test_secret_encryption_key_ring_loads_from_file(tmp_path: Path) -> None:
    """A mounted JSON file supplies protected managed-secret keys."""
    settings = Settings(
        secret_encryption_active_key_id="primary",
        secret_encryption_keys_file=write_secret(tmp_path / "key-ring", '{"primary":"c2VjcmV0"}'),
    )

    assert settings.secret_encryption_keys["primary"].get_secret_value() == "c2VjcmV0"
    assert "secret_encryption_keys_file" not in settings.model_dump()


def test_direct_database_password_takes_precedence_over_file(tmp_path: Path) -> None:
    """A direct database password avoids reading or exposing an unused file."""
    settings = Settings(
        database_url="postgresql://shepherd:direct@postgres/shepherd",
        database_password_file=tmp_path / "missing",
    )

    assert settings.database_url == "postgresql://shepherd:direct@postgres/shepherd"


def test_direct_key_ring_takes_precedence_over_file(tmp_path: Path) -> None:
    """A direct key ring avoids reading or exposing an unused file."""
    settings = Settings(
        secret_encryption_keys={"primary": "direct"},
        secret_encryption_keys_file=tmp_path / "missing",
    )

    assert settings.secret_encryption_keys["primary"].get_secret_value() == "direct"


def test_missing_secret_file_fails_without_secret_content(tmp_path: Path) -> None:
    """Unreadable secret files produce a stable configuration error."""
    with pytest.raises(ValidationError, match="Could not read database password file"):
        Settings(
            database_url="postgresql://shepherd@postgres/shepherd",
            database_password_file=tmp_path / "missing",
        )
