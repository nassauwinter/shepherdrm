"""Define validated request, response, and filtering models for resources."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SharingMode = Literal["Exclusive", "Shared"]
OperationalStatus = Literal["Active", "Cleaning", "Quarantined", "Disabled"]
VisibilityMode = Literal["Public", "Restricted"]
ResourceName = Annotated[str, Field(min_length=1, max_length=255)]
ResourceType = Annotated[str, Field(min_length=1, max_length=255)]


class StrictModel(BaseModel):
    """Reject fields that are not part of the resource contract."""

    model_config = ConfigDict(extra="forbid")


class ResourceCreate(StrictModel):
    """Describe an administrator-created allocatable resource."""

    name: ResourceName
    type: ResourceType
    sharing_mode: SharingMode
    visibility_mode: VisibilityMode = "Restricted"
    labels: dict[str, str] = Field(default_factory=dict)

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
    labels: dict[str, str] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, data: object) -> object:
        """Allow fields to be omitted while rejecting contract-invalid null values."""
        if isinstance(data, dict):
            nullable_updates = {"name", "type", "sharing_mode", "visibility_mode", "labels"}
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
