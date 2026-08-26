"""Define environment-backed runtime configuration for the service."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SHEPHERD_",
        extra="ignore",
    )

    database_url: str = "postgresql://shepherd_rm:shepherd_rm_local@localhost:5432/shepherd_rm"
    database_connect_timeout_seconds: int = Field(default=5, ge=1)
    login_token_ttl_seconds: int = Field(default=43_200, ge=60)
    lease_expiration_poll_seconds: float = Field(default=1.0, gt=0)
    lease_expiration_batch_size: int = Field(default=100, ge=1, le=1000)
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"
    openapi_path: Path = PROJECT_ROOT / "openapi" / "openapi.yaml"
    migrations_path: Path = PROJECT_ROOT / "migrations"


@lru_cache
def get_settings() -> Settings:
    return Settings()
