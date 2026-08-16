"""Provide shared SQLAlchemy persistence operations for the identity feature."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from shepherd_rm.audit import record_audit_event
from shepherd_rm.database_models import Group, PasswordCredential, Principal
from shepherd_rm.identity.authentication import hash_password_async
from shepherd_rm.identity.constants import ADMINISTRATION_LOCK_ID
from shepherd_rm.identity.models import (
    GroupCreate,
    GroupResponse,
    GroupUpdate,
    PrincipalCreate,
    PrincipalResponse,
    PrincipalUpdate,
)

PrincipalKind = Literal["User", "Service"]


def principal_from_row(row: RowMapping) -> PrincipalResponse:
    """Validate a principal returned by a canonical principal statement."""
    return PrincipalResponse.model_validate(dict(row))


def group_from_row(row: RowMapping) -> GroupResponse:
    """Validate a group returned by a canonical group statement."""
    return GroupResponse.model_validate(dict(row))


async def fetch_principal(
    connection: AsyncConnection,
    principal_id: uuid.UUID,
    kind: PrincipalKind,
) -> PrincipalResponse:
    """Return a principal of the requested kind or raise a public not-found error."""
    result = await connection.execute(
        select(Principal.__table__).where(Principal.id == principal_id, Principal.kind == kind)
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Principal not found")
    return principal_from_row(row)


async def create_principal(
    connection: AsyncConnection,
    request: PrincipalCreate,
    kind: PrincipalKind,
    actor_id: uuid.UUID,
) -> PrincipalResponse:
    """Create a principal and its optional password in the caller's transaction."""
    if kind == "User" and request.password is None:
        raise HTTPException(status_code=400, detail="A password is required for a user")
    if kind == "Service" and request.password is not None:
        raise HTTPException(status_code=400, detail="Service identities cannot have passwords")
    principal_id = uuid.uuid4()
    result = await connection.execute(
        insert(Principal)
        .values(
            id=principal_id,
            kind=kind,
            role=request.role,
            name=request.name,
            display_name=request.display_name,
        )
        .returning(Principal.__table__)
    )
    row = result.mappings().one()
    if request.password is not None:
        await connection.execute(
            insert(PasswordCredential).values(
                principal_id=principal_id,
                password_hash=await hash_password_async(request.password),
            )
        )
    await record_audit_event(connection, actor_id, "principal.created", "Principal", principal_id)
    return principal_from_row(row)


async def list_principals(
    connection: AsyncConnection, kind: PrincipalKind
) -> list[PrincipalResponse]:
    """List principals of one kind in deterministic order."""
    result = await connection.execute(
        select(Principal.__table__)
        .where(Principal.kind == kind)
        .order_by(Principal.name, Principal.id)
    )
    return [principal_from_row(row) for row in result.mappings()]


async def update_principal(
    connection: AsyncConnection,
    principal_id: uuid.UUID,
    request: PrincipalUpdate,
    kind: PrincipalKind,
    actor_id: uuid.UUID,
) -> PrincipalResponse:
    """Update public principal fields and append the audit event atomically."""
    changes = request.model_dump(exclude_none=True)
    if not changes:
        return await fetch_principal(connection, principal_id, kind)
    result = await connection.execute(
        update(Principal)
        .where(Principal.id == principal_id, Principal.kind == kind)
        .values(**changes)
        .returning(Principal.__table__)
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Principal not found")
    await record_audit_event(connection, actor_id, "principal.updated", "Principal", principal_id)
    return principal_from_row(row)


async def archive_principal(
    connection: AsyncConnection,
    principal_id: uuid.UUID,
    kind: PrincipalKind,
    actor_id: uuid.UUID,
) -> None:
    """Archive a principal and record the action atomically."""
    if kind == "User":
        await connection.execute(select(func.pg_advisory_xact_lock(ADMINISTRATION_LOCK_ID)))
    target = (
        await connection.execute(
            select(Principal.id, Principal.role)
            .where(Principal.id == principal_id, Principal.kind == kind)
            .with_for_update()
        )
    ).one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Principal not found")
    if kind == "User" and target.role == "Admin":
        active_administrators = (
            (
                await connection.execute(
                    select(Principal.id)
                    .join(PasswordCredential, PasswordCredential.principal_id == Principal.id)
                    .where(
                        Principal.kind == "User",
                        Principal.role == "Admin",
                        Principal.archived_at.is_(None),
                    )
                    .with_for_update(of=Principal)
                )
            )
            .scalars()
            .all()
        )
        if active_administrators == [principal_id]:
            raise HTTPException(
                status_code=409,
                detail="The final active administrator cannot be archived",
            )
    result = await connection.execute(
        update(Principal)
        .where(Principal.id == principal_id, Principal.kind == kind)
        .values(archived_at=func.coalesce(Principal.archived_at, func.now()))
        .returning(Principal.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Principal not found")
    await record_audit_event(connection, actor_id, "principal.archived", "Principal", principal_id)


async def fetch_group(connection: AsyncConnection, group_id: uuid.UUID) -> GroupResponse:
    """Return a group or raise a public not-found error."""
    result = await connection.execute(select(Group.__table__).where(Group.id == group_id))
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return group_from_row(row)


async def create_group(
    connection: AsyncConnection,
    request: GroupCreate,
    actor_id: uuid.UUID,
) -> GroupResponse:
    """Create a group and its audit event atomically."""
    group_id = uuid.uuid4()
    result = await connection.execute(
        insert(Group)
        .values(id=group_id, name=request.name, description=request.description)
        .returning(Group.__table__)
    )
    row = result.mappings().one()
    await record_audit_event(connection, actor_id, "group.created", "Group", group_id)
    return group_from_row(row)


async def list_groups(connection: AsyncConnection) -> list[GroupResponse]:
    """List groups in deterministic order."""
    result = await connection.execute(select(Group.__table__).order_by(Group.name, Group.id))
    return [group_from_row(row) for row in result.mappings()]


async def update_group(
    connection: AsyncConnection,
    group_id: uuid.UUID,
    request: GroupUpdate,
    actor_id: uuid.UUID,
) -> GroupResponse:
    """Update a group description and append its audit event atomically."""
    changes = request.model_dump(exclude_unset=True)
    if not changes:
        return await fetch_group(connection, group_id)
    result = await connection.execute(
        update(Group).where(Group.id == group_id).values(**changes).returning(Group.__table__)
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Group not found")
    await record_audit_event(connection, actor_id, "group.updated", "Group", group_id)
    return group_from_row(row)


async def archive_group(
    connection: AsyncConnection,
    group_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> None:
    """Archive a group and append its audit event atomically."""
    result = await connection.execute(
        update(Group)
        .where(Group.id == group_id)
        .values(archived_at=func.coalesce(Group.archived_at, func.now()))
        .returning(Group.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Group not found")
    await record_audit_event(connection, actor_id, "group.archived", "Group", group_id)
