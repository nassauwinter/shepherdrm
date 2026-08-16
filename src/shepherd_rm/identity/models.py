"""Define validated request and response models for identity operations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class PasswordRequest(StrictModel):
    password: str = Field(min_length=12, max_length=1024)


class PrincipalResponse(StrictModel):
    id: UUID
    kind: Literal["User", "Service"]
    role: Literal["Admin", "User"]
    name: str
    display_name: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PrincipalCreate(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    role: Literal["Admin", "User"] = "User"
    password: str | None = Field(default=None, min_length=12, max_length=1024)


class PrincipalUpdate(StrictModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: Literal["Admin", "User"] | None = None


class TokenCreate(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        """Reject ambiguous expiration timestamps that have no UTC offset."""
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("expires_at must include a timezone offset")
        return value


class TokenResponse(StrictModel):
    id: UUID
    name: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class TokenIssued(TokenResponse):
    token: str


class LoginResponse(StrictModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class GroupCreate(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class GroupUpdate(StrictModel):
    description: str | None = None


class GroupResponse(StrictModel):
    id: UUID
    name: str
    description: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
