"""Define validated resource-secret request and response representations."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

SecretName = Annotated[str, Field(min_length=1, max_length=255)]
SecretValue = Annotated[str, Field(min_length=1, max_length=65_536)]


class StrictModel(BaseModel):
    """Reject fields outside the authoritative resource-secret contract."""

    model_config = ConfigDict(extra="forbid")


class ManagedMaterial(StrictModel):
    """Carry plaintext only in an incoming managed-secret write."""

    mode: Literal["Managed"]
    value: SecretValue


class ExternalMaterial(StrictModel):
    """Identify externally stored secret material without resolving its value."""

    mode: Literal["External"]
    provider: Annotated[str, Field(min_length=1, max_length=255)]
    reference: Annotated[str, Field(min_length=1, max_length=2048)]


SecretMaterial = Annotated[ManagedMaterial | ExternalMaterial, Field(discriminator="mode")]


class ResourceSecretCreate(StrictModel):
    """Describe an administrator-created secret associated with one resource."""

    name: SecretName
    description: Annotated[str, Field(max_length=1000)] | None = None
    material: SecretMaterial


class ResourceSecretUpdate(StrictModel):
    """Apply an optimistic metadata or material replacement to a resource secret."""

    version: int = Field(ge=1)
    name: SecretName | None = None
    description: Annotated[str, Field(max_length=1000)] | None = None
    material: SecretMaterial | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_name_or_material(cls, data: object) -> object:
        """Allow omission while rejecting null for non-nullable update fields."""
        if isinstance(data, dict) and any(
            key in data and data[key] is None for key in ("name", "material")
        ):
            raise ValueError("name and material cannot be null")
        return data


class ResourceSecretMetadata(StrictModel):
    """Expose safe secret descriptors without material or external references."""

    id: UUID
    resource_id: UUID
    name: str
    description: str | None
    mode: Literal["Managed", "External"]
    version: int
    created_at: datetime
    updated_at: datetime


class ManagedSecretAccess(StrictModel):
    """Return managed material only from an explicitly authorized access operation."""

    id: UUID
    name: str
    mode: Literal["Managed"]
    value: str


class ExternalSecretAccess(StrictModel):
    """Return an external reference only from an authorized access operation."""

    id: UUID
    name: str
    mode: Literal["External"]
    provider: str
    reference: str


SecretAccess = Annotated[
    ManagedSecretAccess | ExternalSecretAccess,
    Field(discriminator="mode"),
]
