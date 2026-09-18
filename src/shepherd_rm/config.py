"""Define environment-backed runtime configuration for the service."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SHEPHERD_",
        extra="ignore",
    )

    database_url: str = "postgresql://shepherd_rm:shepherd_rm_local@localhost:5432/shepherd_rm"
    database_password_file: Path | None = Field(default=None, exclude=True, repr=False)
    database_connect_timeout_seconds: int = Field(default=5, ge=1)
    max_request_body_bytes: int = Field(default=1_048_576, ge=1, le=104_857_600)
    login_token_ttl_seconds: int = Field(default=43_200, ge=60)
    login_rate_limit_attempts: int = Field(default=10, ge=1, le=10_000)
    login_rate_limit_window_seconds: int = Field(default=300, ge=1, le=86_400)
    secret_access_rate_limit_attempts: int = Field(default=60, ge=1, le=10_000)
    secret_access_rate_limit_window_seconds: int = Field(default=60, ge=1, le=86_400)
    lease_expiration_poll_seconds: float = Field(default=1.0, gt=0)
    lease_expiration_batch_size: int = Field(default=100, ge=1, le=1000)
    secret_encryption_active_key_id: str | None = None
    secret_encryption_keys: dict[str, SecretStr] = Field(default_factory=dict)
    secret_encryption_keys_file: Path | None = Field(default=None, exclude=True, repr=False)
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"
    openapi_path: Path = PROJECT_ROOT / "openapi" / "openapi.yaml"
    migrations_path: Path = PROJECT_ROOT / "migrations"

    @model_validator(mode="before")
    @classmethod
    def load_secret_files(cls, values: Any) -> Any:
        """Resolve supported file-backed secrets without exposing their contents."""
        if not isinstance(values, dict):
            return values
        resolved = dict(values)

        password_file = resolved.get("database_password_file")
        if password_file is not None:
            direct_database_url = resolved.get("database_url")
            database_url = str(direct_database_url or cls.model_fields["database_url"].default)
            try:
                parsed_url = make_url(database_url)
            except ArgumentError as error:
                raise ValueError("Database URL is invalid") from error
            if direct_database_url is None or parsed_url.password is None:
                password = cls._read_secret_file(password_file, "database password")
                resolved["database_url"] = parsed_url.set(password=password).render_as_string(
                    hide_password=False
                )

        key_ring_file = resolved.get("secret_encryption_keys_file")
        if key_ring_file is not None and "secret_encryption_keys" not in resolved:
            encoded_key_ring = cls._read_secret_file(key_ring_file, "secret encryption key ring")
            try:
                key_ring = json.loads(encoded_key_ring)
            except json.JSONDecodeError as error:
                raise ValueError(
                    "Secret encryption key ring file must contain valid JSON"
                ) from error
            if not isinstance(key_ring, dict):
                raise ValueError("Secret encryption key ring file must contain a JSON object")
            resolved["secret_encryption_keys"] = key_ring

        return resolved

    @staticmethod
    def _read_secret_file(path: str | Path, description: str) -> str:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise ValueError(f"Could not read {description} file") from error
        if not value:
            raise ValueError(f"{description.capitalize()} file must not be empty")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
