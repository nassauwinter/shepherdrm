"""Define validated request, response, and filtering models for resources."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SharingMode = Literal["Exclusive", "Shared"]
OperationalStatus = Literal["Active", "Cleaning", "Quarantined", "Disabled"]
VisibilityMode = Literal["Public", "Restricted"]
ExpirationMode = Literal["Required", "Optional"]
ResourceName = Annotated[str, Field(min_length=1, max_length=255)]
ResourceType = Annotated[str, Field(min_length=1, max_length=255)]
TtlSeconds = Annotated[int, Field(ge=1)]


def validate_expiration_policy(
    expiration_mode: ExpirationMode,
    default_ttl_seconds: int | None,
    max_ttl_seconds: int | None,
) -> None:
    """Reject expiration policies that cannot resolve a valid resource lease."""
    if expiration_mode == "Required" and default_ttl_seconds is None:
        raise ValueError("Required expiration needs a default TTL")
    if (
        default_ttl_seconds is not None
        and max_ttl_seconds is not None
        and default_ttl_seconds > max_ttl_seconds
    ):
        raise ValueError("Default TTL cannot exceed maximum TTL")


class StrictModel(BaseModel):
    """Reject fields that are not part of the resource contract."""

    model_config = ConfigDict(extra="forbid")


class ResourceCreate(StrictModel):
    """Describe an administrator-created allocatable resource."""

    name: ResourceName
    type: ResourceType
    sharing_mode: SharingMode
    visibility_mode: VisibilityMode = "Restricted"
    expiration_mode: ExpirationMode = "Optional"
    default_ttl_seconds: TtlSeconds | None = None
    max_ttl_seconds: TtlSeconds | None = None
    labels: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_policy(self) -> ResourceCreate:
        """Validate the complete policy supplied when creating a resource."""
        validate_expiration_policy(
            self.expiration_mode, self.default_ttl_seconds, self.max_ttl_seconds
        )
        return self

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, labels: dict[str, str]) -> dict[str, str]:
        """Keep label selectors bounded and unambiguous."""
        if len(labels) > 64:
            raise ValueError("at most 64 labels are allowed")
        if any(not key or len(key) > 128 for key in labels):
            raise ValueError("label keys must contain 1 to 128 characters")
        if any(len(value) > 255 for value in labels.values()):
            raise ValueError("label values must contain at most 255 characters")
        return labels


class ResourceUpdate(StrictModel):
    """Apply an optimistic-concurrency update to resource metadata."""

    version: int = Field(ge=1)
    name: ResourceName | None = None
    type: ResourceType | None = None
    sharing_mode: SharingMode | None = None
    visibility_mode: VisibilityMode | None = None
    expiration_mode: ExpirationMode | None = None
    default_ttl_seconds: TtlSeconds | None = None
    max_ttl_seconds: TtlSeconds | None = None
    labels: dict[str, str] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, data: object) -> object:
        """Allow fields to be omitted while rejecting contract-invalid null values."""
        if isinstance(data, dict):
            nullable_updates = {
                "name",
                "type",
                "sharing_mode",
                "visibility_mode",
                "expiration_mode",
                "labels",
            }
            if any(key in data and data[key] is None for key in nullable_updates):
                raise ValueError("resource update fields cannot be null")
        return data

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, labels: dict[str, str] | None) -> dict[str, str] | None:
        """Apply resource creation's label bounds to replacements."""
        return ResourceCreate.validate_labels(labels) if labels is not None else None


class ResourceResponse(StrictModel):
    """Represent stored resource data plus its derived allocation state."""

    id: UUID
    name: str
    type: str
    labels: dict[str, str]
    sharing_mode: SharingMode
    visibility_mode: VisibilityMode
    expiration_mode: ExpirationMode
    default_ttl_seconds: int | None
    max_ttl_seconds: int | None
    operational_status: OperationalStatus
    version: int
    available: bool
    active_lease_count: int
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ResourcePage(StrictModel):
    """Return a stable offset-based page of matching resources."""

    items: list[ResourceResponse]
    offset: int
    limit: int
    total: int


class ResourceAccess(StrictModel):
    """List administrator-managed principals and groups granted resource access."""

    principal_ids: list[UUID]
    group_ids: list[UUID]
