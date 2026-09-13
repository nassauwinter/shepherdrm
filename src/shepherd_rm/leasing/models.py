"""Define validated lease acquisition, lifecycle, and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shepherd_rm.catalog.models import (
    MAX_TTL_SECONDS,
    ResourceCreate,
    ResourceResponse,
    SharingMode,
)

LeaseState = Literal["Active", "Released", "Expired", "Revoked"]
TtlSeconds = Annotated[int, Field(strict=True, ge=1, le=MAX_TTL_SECONDS)]


class StrictModel(BaseModel):
    """Reject fields outside the authoritative lease contract."""

    model_config = ConfigDict(extra="forbid")


class LeaseCreate(StrictModel):
    """Describe a visibility-aware request for one matching resource."""

    resource_type: Annotated[str, Field(min_length=1, max_length=255)]
    sharing_mode: SharingMode
    labels: dict[str, str] = Field(default_factory=dict)
    ttl_seconds: TtlSeconds | None = None
    consumer: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, labels: dict[str, str]) -> dict[str, str]:
        """Apply catalog label bounds to acquisition selectors."""
        return ResourceCreate.validate_labels(labels)


class LeaseRenew(StrictModel):
    """Request a new expiration resolved from the resource policy and server time."""

    ttl_seconds: TtlSeconds | None = None


class LeaseResponse(StrictModel):
    """Represent a lease and its selected resource without resource secrets."""

    id: UUID
    resource: ResourceResponse
    acquired_by: UUID
    consumer: str | None
    state: LeaseState
    acquired_at: datetime
    expires_at: datetime | None
    last_renewed_at: datetime | None
    ended_at: datetime | None
    end_reason: Literal["Released", "Expired", "Revoked"] | None
    duration_seconds: float
    metadata: dict[str, Any]


class LeasePage(StrictModel):
    """Return a deterministic offset-based page of authorized leases."""

    items: list[LeaseResponse]
    offset: int
    limit: int
    total: int
