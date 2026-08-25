"""Implement transactional resource catalog queries and state changes."""

from __future__ import annotations

import uuid
from typing import Any, Literal, cast

from fastapi import HTTPException
from sqlalchemy import Select, Table, and_, case, delete, exists, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from shepherd_rm.audit import record_audit_event
from shepherd_rm.catalog.models import (
    ResourceAccess,
    ResourceCreate,
    ResourcePage,
    ResourceResponse,
    ResourceUpdate,
    validate_expiration_policy,
)
from shepherd_rm.database_models import (
    Group,
    GroupMembership,
    Lease,
    Principal,
    Resource,
    ResourceGroupGrant,
    ResourcePrincipalGrant,
)

Transition = Literal["disable", "enable", "quarantine", "recover"]
ACTIVE_LEASE = and_(
    Lease.ended_at.is_(None),
    or_(Lease.expires_at.is_(None), Lease.expires_at > func.now()),
)


def resource_visibility_predicate(principal_id: uuid.UUID) -> ColumnElement[bool]:
    """Return the shared public, direct-grant, or active-group visibility rule."""
    direct_grant = exists(
        select(ResourcePrincipalGrant.resource_id).where(
            ResourcePrincipalGrant.resource_id == Resource.id,
            ResourcePrincipalGrant.principal_id == principal_id,
        )
    )
    group_grant = exists(
        select(ResourceGroupGrant.resource_id)
        .join(Group, Group.id == ResourceGroupGrant.group_id)
        .join(GroupMembership, GroupMembership.group_id == ResourceGroupGrant.group_id)
        .where(
            ResourceGroupGrant.resource_id == Resource.id,
            GroupMembership.principal_id == principal_id,
            Group.archived_at.is_(None),
        )
    )
    return or_(Resource.visibility_mode == "Public", direct_grant, group_grant)


def resource_statement() -> Select[tuple[Any, ...]]:
    """Build the canonical resource projection with derived lease information."""
    active_count = func.count(Lease.id).filter(ACTIVE_LEASE)
    return (
        select(
            Resource.id,
            Resource.name,
            Resource.type,
            Resource.labels,
            Resource.sharing_mode,
            Resource.visibility_mode,
            Resource.expiration_mode,
            Resource.default_ttl_seconds,
            Resource.max_ttl_seconds,
            Resource.operational_status,
            Resource.version,
            Resource.archived_at,
            Resource.created_at,
            Resource.updated_at,
            active_count.label("active_lease_count"),
            case(
                (
                    and_(
                        Resource.archived_at.is_(None),
                        Resource.operational_status == "Active",
                        (Resource.sharing_mode == "Shared") | (active_count == 0),
                    ),
                    True,
                ),
                else_=False,
            ).label("available"),
        )
        .outerjoin(Lease, Lease.resource_id == Resource.id)
        .group_by(Resource.id)
    )


def resource_from_row(row: RowMapping) -> ResourceResponse:
    """Validate one canonical resource query result."""
    return ResourceResponse.model_validate(dict(row))


async def fetch_resource(connection: AsyncConnection, resource_id: uuid.UUID) -> ResourceResponse:
    """Return a resource including archived records or raise not found."""
    row = (
        (await connection.execute(resource_statement().where(Resource.id == resource_id)))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return resource_from_row(row)


async def fetch_visible_resource(
    connection: AsyncConnection, resource_id: uuid.UUID, principal_id: uuid.UUID
) -> ResourceResponse:
    """Return a visible non-archived resource without disclosing inaccessible records."""
    row = (
        (
            await connection.execute(
                resource_statement().where(
                    Resource.id == resource_id,
                    Resource.archived_at.is_(None),
                    resource_visibility_predicate(principal_id),
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return resource_from_row(row)


async def create_resource(
    connection: AsyncConnection,
    request: ResourceCreate,
    actor_id: uuid.UUID,
) -> ResourceResponse:
    """Create a resource and its audit event atomically."""
    resource_id = uuid.uuid4()
    await connection.execute(insert(Resource).values(id=resource_id, **request.model_dump()))
    await record_audit_event(connection, actor_id, "resource.created", "Resource", resource_id)
    return await fetch_resource(connection, resource_id)


async def list_resources(
    connection: AsyncConnection,
    *,
    resource_type: str | None,
    sharing_mode: str | None,
    operational_status: str | None,
    available: bool | None,
    labels: dict[str, str],
    include_archived: bool,
    principal_id: uuid.UUID,
    administrator: bool,
    offset: int,
    limit: int,
) -> ResourcePage:
    """List a deterministic page matching exact resource criteria."""
    statement = resource_statement()
    filters: list[ColumnElement[bool]] = []
    if not include_archived:
        filters.append(Resource.archived_at.is_(None))
    if resource_type is not None:
        filters.append(Resource.type == resource_type)
    if sharing_mode is not None:
        filters.append(Resource.sharing_mode == sharing_mode)
    if operational_status is not None:
        filters.append(Resource.operational_status == operational_status)
    if labels:
        filters.append(Resource.labels.contains(labels))
    if not administrator:
        filters.append(resource_visibility_predicate(principal_id))
    catalog = statement.where(*filters).subquery("matching_resources")
    filtered = select(catalog)
    if available is not None:
        filtered = filtered.where(catalog.c.available.is_(available))
    total = await connection.scalar(select(func.count()).select_from(filtered.subquery()))
    rows = (
        (
            await connection.execute(
                filtered.order_by(catalog.c.name, catalog.c.id).offset(offset).limit(limit)
            )
        )
        .mappings()
        .all()
    )
    resources = [resource_from_row(row) for row in rows]
    return ResourcePage(items=resources, offset=offset, limit=limit, total=total or 0)


async def lock_managed_resource(connection: AsyncConnection, resource_id: uuid.UUID) -> RowMapping:
    """Lock a non-archived resource before changing its access grants."""
    target = (
        (
            await connection.execute(
                select(Resource.__table__).where(Resource.id == resource_id).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    if target.archived_at is not None:
        raise HTTPException(status_code=409, detail="Archived resources cannot change access")
    return target


async def fetch_resource_access(
    connection: AsyncConnection, resource_id: uuid.UUID
) -> ResourceAccess:
    """List direct principal and group grants for administrator inspection."""
    await fetch_resource(connection, resource_id)
    principal_ids = (
        (
            await connection.execute(
                select(ResourcePrincipalGrant.principal_id)
                .where(ResourcePrincipalGrant.resource_id == resource_id)
                .order_by(ResourcePrincipalGrant.principal_id)
            )
        )
        .scalars()
        .all()
    )
    group_ids = (
        (
            await connection.execute(
                select(ResourceGroupGrant.group_id)
                .where(ResourceGroupGrant.resource_id == resource_id)
                .order_by(ResourceGroupGrant.group_id)
            )
        )
        .scalars()
        .all()
    )
    return ResourceAccess(principal_ids=list(principal_ids), group_ids=list(group_ids))


async def set_resource_grant(
    connection: AsyncConnection,
    resource_id: uuid.UUID,
    target_id: uuid.UUID,
    target_type: Literal["Principal", "Group"],
    granted: bool,
    actor_id: uuid.UUID,
) -> None:
    """Idempotently add or remove one grant while holding the resource row lock."""
    await lock_managed_resource(connection, resource_id)
    grant_table: Table
    if target_type == "Principal":
        target_exists = await connection.scalar(
            select(Principal.id).where(Principal.id == target_id, Principal.archived_at.is_(None))
        )
        grant_table = cast(Table, ResourcePrincipalGrant.__table__)
        target_column = ResourcePrincipalGrant.principal_id
        target_key = "principal_id"
    else:
        target_exists = await connection.scalar(
            select(Group.id).where(Group.id == target_id, Group.archived_at.is_(None))
        )
        grant_table = cast(Table, ResourceGroupGrant.__table__)
        target_column = ResourceGroupGrant.group_id
        target_key = "group_id"
    if granted and target_exists is None:
        raise HTTPException(status_code=404, detail=f"{target_type} not found")

    if granted:
        result = await connection.execute(
            postgresql_insert(grant_table)
            .values(resource_id=resource_id, **{target_key: target_id})
            .on_conflict_do_nothing()
            .returning(target_column)
        )
    else:
        result = await connection.execute(
            delete(grant_table)
            .where(grant_table.c.resource_id == resource_id, target_column == target_id)
            .returning(target_column)
        )
    if result.scalar_one_or_none() is not None:
        action = "granted" if granted else "revoked"
        await record_audit_event(
            connection,
            actor_id,
            f"resource.access.{action}",
            "Resource",
            resource_id,
            metadata={"target_type": target_type, "target_id": str(target_id)},
        )


async def update_resource(
    connection: AsyncConnection,
    resource_id: uuid.UUID,
    request: ResourceUpdate,
    actor_id: uuid.UUID,
) -> ResourceResponse:
    """Update resource metadata while locking allocatability and checking its version."""
    target = (
        (
            await connection.execute(
                select(Resource.__table__).where(Resource.id == resource_id).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    if target.archived_at is not None:
        raise HTTPException(status_code=409, detail="Archived resources cannot be updated")
    if target.version != request.version:
        raise HTTPException(status_code=409, detail="Resource version is stale")
    changes = request.model_dump(exclude_unset=True, exclude={"version"})
    if not changes:
        return await fetch_resource(connection, resource_id)
    try:
        validate_expiration_policy(
            changes.get("expiration_mode", target.expiration_mode),
            changes.get("default_ttl_seconds", target.default_ttl_seconds),
            changes.get("max_ttl_seconds", target.max_ttl_seconds),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    await connection.execute(
        update(Resource)
        .where(Resource.id == resource_id)
        .values(**changes, version=Resource.version + 1)
    )
    await record_audit_event(connection, actor_id, "resource.updated", "Resource", resource_id)
    return await fetch_resource(connection, resource_id)


async def archive_resource(
    connection: AsyncConnection, resource_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    """Archive an unlocked resource only when it has no active leases."""
    target = (
        (
            await connection.execute(
                select(Resource.__table__).where(Resource.id == resource_id).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    if target.archived_at is not None:
        return
    active_lease = await connection.scalar(
        select(Lease.id).where(Lease.resource_id == resource_id, ACTIVE_LEASE).limit(1)
    )
    if active_lease is not None:
        raise HTTPException(
            status_code=409, detail="A resource with active leases cannot be archived"
        )
    await connection.execute(
        update(Resource)
        .where(Resource.id == resource_id)
        .values(archived_at=func.now(), version=Resource.version + 1)
    )
    await record_audit_event(connection, actor_id, "resource.archived", "Resource", resource_id)


async def transition_resource(
    connection: AsyncConnection,
    resource_id: uuid.UUID,
    transition: Transition,
    actor_id: uuid.UUID,
) -> ResourceResponse:
    """Apply one legal administrator-controlled operational transition."""
    allowed = {
        "disable": ({"Active", "Quarantined"}, "Disabled"),
        "enable": ({"Disabled"}, "Active"),
        "quarantine": ({"Active"}, "Quarantined"),
        "recover": ({"Quarantined"}, "Active"),
    }
    target = (
        (
            await connection.execute(
                select(Resource.__table__).where(Resource.id == resource_id).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    if target.archived_at is not None:
        raise HTTPException(status_code=409, detail="Archived resources cannot change state")
    sources, destination = allowed[transition]
    if target.operational_status not in sources:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot {transition} a resource in {target.operational_status} state",
        )
    await connection.execute(
        update(Resource)
        .where(Resource.id == resource_id)
        .values(operational_status=destination, version=Resource.version + 1)
    )
    await record_audit_event(
        connection,
        actor_id,
        {
            "disable": "resource.disabled",
            "enable": "resource.enabled",
            "quarantine": "resource.quarantined",
            "recover": "resource.recovered",
        }[transition],
        "Resource",
        resource_id,
    )
    return await fetch_resource(connection, resource_id)
