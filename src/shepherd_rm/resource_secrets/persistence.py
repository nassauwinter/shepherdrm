"""Implement transactional resource-secret management and authorized disclosure."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from shepherd_rm.audit import record_audit_event
from shepherd_rm.catalog.persistence import resource_visibility_predicate
from shepherd_rm.database_models import Lease, Resource, ResourceSecret
from shepherd_rm.resource_secrets.crypto import SecretCipher
from shepherd_rm.resource_secrets.models import (
    ExternalMaterial,
    ExternalSecretAccess,
    ManagedMaterial,
    ManagedSecretAccess,
    ResourceSecretCreate,
    ResourceSecretMetadata,
    ResourceSecretUpdate,
    SecretAccess,
    SecretMaterial,
)


def _metadata_from_row(row: RowMapping) -> ResourceSecretMetadata:
    """Project a database row without any stored secret material."""
    return ResourceSecretMetadata.model_validate(
        {key: row[key] for key in ResourceSecretMetadata.model_fields}
    )


async def _lock_resource(
    connection: AsyncConnection, resource_id: uuid.UUID, *, require_active: bool
) -> RowMapping:
    """Lock a resource and optionally reject its archived state."""
    row = (
        (
            await connection.execute(
                select(Resource.__table__).where(Resource.id == resource_id).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    if require_active and row.archived_at is not None:
        raise HTTPException(status_code=409, detail="Archived resources cannot change secrets")
    return row


async def _lock_secret(
    connection: AsyncConnection, resource_id: uuid.UUID, secret_id: uuid.UUID
) -> RowMapping:
    """Lock one secret while ensuring it belongs to the addressed resource."""
    row = (
        (
            await connection.execute(
                select(ResourceSecret.__table__)
                .where(ResourceSecret.id == secret_id, ResourceSecret.resource_id == resource_id)
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Resource secret not found")
    return row


def _material_values(
    cipher: SecretCipher,
    material: SecretMaterial,
    resource_id: uuid.UUID,
    secret_id: uuid.UUID,
) -> dict[str, Any]:
    """Convert one complete material replacement into mutually exclusive columns."""
    if isinstance(material, ManagedMaterial):
        encrypted = cipher.encrypt(material.value, resource_id, secret_id)
        return {
            "mode": "Managed",
            "encrypted_value": encrypted.envelope,
            "encryption_key_id": encrypted.key_id,
            "external_provider": None,
            "external_reference": None,
        }
    assert isinstance(material, ExternalMaterial)
    return {
        "mode": "External",
        "encrypted_value": None,
        "encryption_key_id": None,
        "external_provider": material.provider,
        "external_reference": material.reference,
    }


async def create_resource_secret(
    connection: AsyncConnection,
    cipher: SecretCipher,
    resource_id: uuid.UUID,
    request: ResourceSecretCreate,
    actor_id: uuid.UUID,
) -> ResourceSecretMetadata:
    """Create secret material and its safe audit event atomically."""
    await _lock_resource(connection, resource_id, require_active=True)
    secret_id = uuid.uuid4()
    values = _material_values(cipher, request.material, resource_id, secret_id)
    row = (
        (
            await connection.execute(
                insert(ResourceSecret)
                .values(
                    id=secret_id,
                    resource_id=resource_id,
                    name=request.name,
                    description=request.description,
                    **values,
                )
                .returning(*ResourceSecret.__table__.c)
            )
        )
        .mappings()
        .one()
    )
    await record_audit_event(
        connection,
        actor_id,
        "resource_secret.created",
        "ResourceSecret",
        secret_id,
        metadata={"resource_id": str(resource_id), "mode": request.material.mode},
    )
    return _metadata_from_row(row)


async def list_resource_secrets(
    connection: AsyncConnection, resource_id: uuid.UUID
) -> list[ResourceSecretMetadata]:
    """List safe metadata for all secrets associated with an existing resource."""
    await _lock_resource(connection, resource_id, require_active=False)
    rows = (
        (
            await connection.execute(
                select(ResourceSecret.__table__)
                .where(ResourceSecret.resource_id == resource_id)
                .order_by(ResourceSecret.name, ResourceSecret.id)
            )
        )
        .mappings()
        .all()
    )
    return [_metadata_from_row(row) for row in rows]


async def get_resource_secret(
    connection: AsyncConnection, resource_id: uuid.UUID, secret_id: uuid.UUID
) -> ResourceSecretMetadata:
    """Return safe metadata for one resource secret."""
    await _lock_resource(connection, resource_id, require_active=False)
    return _metadata_from_row(await _lock_secret(connection, resource_id, secret_id))


async def update_resource_secret(
    connection: AsyncConnection,
    cipher: SecretCipher,
    resource_id: uuid.UUID,
    secret_id: uuid.UUID,
    request: ResourceSecretUpdate,
    actor_id: uuid.UUID,
) -> ResourceSecretMetadata:
    """Replace metadata or material after locking and checking the stored version."""
    await _lock_resource(connection, resource_id, require_active=True)
    target = await _lock_secret(connection, resource_id, secret_id)
    if target.version != request.version:
        raise HTTPException(status_code=409, detail="Resource secret version is stale")
    changes = request.model_dump(exclude_unset=True, exclude={"version", "material"})
    if request.material is not None:
        changes.update(_material_values(cipher, request.material, resource_id, secret_id))
    if not changes:
        return _metadata_from_row(target)
    row = (
        (
            await connection.execute(
                update(ResourceSecret)
                .where(ResourceSecret.id == secret_id)
                .values(**changes, version=ResourceSecret.version + 1)
                .returning(*ResourceSecret.__table__.c)
            )
        )
        .mappings()
        .one()
    )
    await record_audit_event(
        connection,
        actor_id,
        "resource_secret.updated",
        "ResourceSecret",
        secret_id,
        metadata={"resource_id": str(resource_id)},
    )
    return _metadata_from_row(row)


async def delete_resource_secret(
    connection: AsyncConnection,
    resource_id: uuid.UUID,
    secret_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> None:
    """Idempotently remove stored material while preserving its audit history."""
    await _lock_resource(connection, resource_id, require_active=False)
    deleted = await connection.execute(
        delete(ResourceSecret)
        .where(ResourceSecret.id == secret_id, ResourceSecret.resource_id == resource_id)
        .returning(ResourceSecret.id)
    )
    if deleted.scalar_one_or_none() is not None:
        await record_audit_event(
            connection,
            actor_id,
            "resource_secret.deleted",
            "ResourceSecret",
            secret_id,
            metadata={"resource_id": str(resource_id)},
        )


async def _access_locked_secret(
    connection: AsyncConnection,
    cipher: SecretCipher,
    secret: RowMapping,
    actor_id: uuid.UUID,
    access_path: Literal["Administration", "Lease"],
    lease_id: uuid.UUID | None = None,
) -> SecretAccess:
    """Reveal locked material and append a value-free audit event."""
    if secret.mode == "Managed":
        if secret.encrypted_value is None or secret.encryption_key_id is None:
            raise RuntimeError("Managed resource secret has incomplete encrypted material")
        result: SecretAccess = ManagedSecretAccess(
            id=secret.id,
            name=secret.name,
            mode="Managed",
            value=cipher.decrypt(
                bytes(secret.encrypted_value),
                secret.encryption_key_id,
                secret.resource_id,
                secret.id,
            ),
        )
    else:
        if secret.external_provider is None or secret.external_reference is None:
            raise RuntimeError("External resource secret has incomplete reference material")
        result = ExternalSecretAccess(
            id=secret.id,
            name=secret.name,
            mode="External",
            provider=secret.external_provider,
            reference=secret.external_reference,
        )
    metadata = {"resource_id": str(secret.resource_id), "access_path": access_path}
    if lease_id is not None:
        metadata["lease_id"] = str(lease_id)
    await record_audit_event(
        connection,
        actor_id,
        "resource_secret.accessed",
        "ResourceSecret",
        secret.id,
        metadata=metadata,
    )
    return result


async def access_resource_secret_as_admin(
    connection: AsyncConnection,
    cipher: SecretCipher,
    resource_id: uuid.UUID,
    secret_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> SecretAccess:
    """Reveal one resource secret through the explicit administrator path."""
    await _lock_resource(connection, resource_id, require_active=False)
    secret = await _lock_secret(connection, resource_id, secret_id)
    return await _access_locked_secret(connection, cipher, secret, actor_id, "Administration")


async def _lock_active_authorized_lease(
    connection: AsyncConnection,
    lease_id: uuid.UUID,
    actor_id: uuid.UUID,
    administrator: bool,
) -> RowMapping:
    """Lock an effectively active lease visible to its owner or an administrator."""
    filters = [
        Lease.id == lease_id,
        Lease.ended_at.is_(None),
        (Lease.expires_at.is_(None)) | (Lease.expires_at > func.now()),
    ]
    if not administrator:
        filters.append(Lease.acquired_by == actor_id)
    row = (
        (await connection.execute(select(Lease.__table__).where(*filters).with_for_update()))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Active lease not found")
    if not administrator:
        locked_resource_id = await connection.scalar(
            select(Resource.id).where(Resource.id == row.resource_id).with_for_update()
        )
        if locked_resource_id is None:
            raise HTTPException(status_code=404, detail="Active lease not found")
        visible_resource_id = await connection.scalar(
            select(Resource.id).where(
                Resource.id == row.resource_id,
                Resource.archived_at.is_(None),
                resource_visibility_predicate(actor_id),
            )
        )
        if visible_resource_id is None:
            raise HTTPException(status_code=404, detail="Active lease not found")
    return row


async def list_lease_secrets(
    connection: AsyncConnection,
    lease_id: uuid.UUID,
    actor_id: uuid.UUID,
    administrator: bool,
) -> list[ResourceSecretMetadata]:
    """List safe secret descriptors through an effectively active lease."""
    lease = await _lock_active_authorized_lease(connection, lease_id, actor_id, administrator)
    rows = (
        (
            await connection.execute(
                select(ResourceSecret.__table__)
                .where(ResourceSecret.resource_id == lease.resource_id)
                .order_by(ResourceSecret.name, ResourceSecret.id)
            )
        )
        .mappings()
        .all()
    )
    return [_metadata_from_row(row) for row in rows]


async def access_lease_secret(
    connection: AsyncConnection,
    cipher: SecretCipher,
    lease_id: uuid.UUID,
    secret_id: uuid.UUID,
    actor_id: uuid.UUID,
    administrator: bool,
) -> SecretAccess:
    """Reveal a secret belonging to an effectively active authorized lease."""
    lease = await _lock_active_authorized_lease(connection, lease_id, actor_id, administrator)
    secret = await _lock_secret(connection, lease.resource_id, secret_id)
    return await _access_locked_secret(connection, cipher, secret, actor_id, "Lease", lease_id)
