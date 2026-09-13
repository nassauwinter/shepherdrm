"""Implement transactional lease selection, lifecycle changes, and expiration."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import Select, case, func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from shepherd_rm.audit import record_audit_event
from shepherd_rm.catalog.persistence import (
    ACTIVE_LEASE,
    fetch_resource,
    resource_visibility_predicate,
)
from shepherd_rm.database_models import Lease, Resource
from shepherd_rm.leasing.models import LeaseCreate, LeasePage, LeaseRenew, LeaseResponse


def _request_hash(request: LeaseCreate) -> str:
    """Return a stable digest that excludes authentication and transport details."""
    payload = request.model_dump(mode="json", exclude_unset=False)
    payload["ttl_seconds_intent"] = (
        "omitted" if "ttl_seconds" not in request.model_fields_set else "explicit"
    )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _state_expression() -> ColumnElement[str]:
    """Map stored terminal reasons to the public state name."""
    return case((Lease.end_reason.is_(None), "Active"), else_=Lease.end_reason)


def lease_statement() -> Select[tuple[Any, ...]]:
    """Build the canonical lease projection used by reads and writes."""
    return select(
        Lease.id,
        Lease.resource_id,
        Lease.acquired_by,
        Lease.consumer,
        Lease.acquired_at,
        Lease.expires_at,
        Lease.last_renewed_at,
        Lease.ended_at,
        Lease.end_reason,
        Lease.metadata_.label("metadata"),
        _state_expression().label("state"),
        func.extract("epoch", func.coalesce(Lease.ended_at, func.now()) - Lease.acquired_at).label(
            "duration_seconds"
        ),
    )


async def _lease_from_row(connection: AsyncConnection, row: RowMapping) -> LeaseResponse:
    """Attach the selected resource to one canonical lease row."""
    resource = await fetch_resource(connection, row.resource_id)
    values = dict(row)
    values.pop("resource_id")
    return LeaseResponse(resource=resource, **values)


def _authorized_lease_filter(principal_id: uuid.UUID, administrator: bool) -> ColumnElement[bool]:
    """Allow administrators to inspect all leases and owners to inspect their own."""
    return Lease.id.is_not(None) if administrator else Lease.acquired_by == principal_id


def _resolve_ttl(
    resource: RowMapping,
    ttl_seconds: int | None,
    supplied: bool,
) -> int | None:
    """Resolve omitted, explicit, and indefinite TTL intent against resource policy."""
    resolved = ttl_seconds if supplied else resource.default_ttl_seconds
    if resolved is None and resource.expiration_mode == "Required":
        raise HTTPException(status_code=409, detail="Selected resource requires expiration")
    if (
        resolved is not None
        and resource.max_ttl_seconds is not None
        and resolved > resource.max_ttl_seconds
    ):
        raise HTTPException(status_code=409, detail="Requested TTL exceeds resource maximum")
    return resolved


async def acquire_lease(
    connection: AsyncConnection,
    request: LeaseCreate,
    idempotency_key: str,
    principal_id: uuid.UUID,
    administrator: bool,
) -> LeaseResponse:
    """Select and lock a matching resource, then create one idempotent lease."""
    request_hash = _request_hash(request)
    lock_key = f"lease:{principal_id}:{idempotency_key}"
    await connection.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0))))
    existing = (
        (
            await connection.execute(
                lease_statement().where(
                    Lease.acquired_by == principal_id,
                    Lease.idempotency_key == idempotency_key,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        stored_hash = await connection.scalar(
            select(Lease.request_hash).where(Lease.id == existing.id)
        )
        if stored_hash != request_hash:
            raise HTTPException(
                status_code=409, detail="Idempotency key was used for another request"
            )
        return await _lease_from_row(connection, existing)

    active_count = (
        select(func.count(Lease.id))
        .where(Lease.resource_id == Resource.id, ACTIVE_LEASE)
        .correlate(Resource)
        .scalar_subquery()
    )
    ttl_supplied = "ttl_seconds" in request.model_fields_set
    filters: list[ColumnElement[bool]] = [
        Resource.archived_at.is_(None),
        Resource.operational_status == "Active",
        Resource.type == request.resource_type,
        Resource.sharing_mode == request.sharing_mode,
        Resource.labels.contains(request.labels),
    ]
    if not administrator:
        filters.append(resource_visibility_predicate(principal_id))
    if ttl_supplied:
        if request.ttl_seconds is None:
            filters.append(Resource.expiration_mode == "Optional")
        else:
            filters.append(
                (Resource.max_ttl_seconds.is_(None))
                | (Resource.max_ttl_seconds >= request.ttl_seconds)
            )
    else:
        filters.append(
            (Resource.default_ttl_seconds.is_not(None)) | (Resource.expiration_mode == "Optional")
        )
    if request.sharing_mode == "Exclusive":
        filters.append(active_count == 0)
    candidate = (
        (
            await connection.execute(
                select(Resource.__table__)
                .where(*filters)
                .order_by(active_count, Resource.last_leased_at.asc().nulls_first(), Resource.id)
                .limit(1)
                .with_for_update(skip_locked=request.sharing_mode == "Exclusive")
            )
        )
        .mappings()
        .one_or_none()
    )
    if candidate is None:
        raise HTTPException(status_code=409, detail="No matching resource is available")
    if request.sharing_mode == "Exclusive":
        still_active = await connection.scalar(
            select(Lease.id).where(Lease.resource_id == candidate.id, ACTIVE_LEASE).limit(1)
        )
        if still_active is not None:
            raise HTTPException(status_code=409, detail="No matching resource is available")

    ttl = _resolve_ttl(candidate, request.ttl_seconds, ttl_supplied)
    acquired_at = datetime.now(UTC)
    lease_id = uuid.uuid4()
    expires_at = acquired_at + timedelta(seconds=ttl) if ttl is not None else None
    row = (
        (
            await connection.execute(
                insert(Lease)
                .values(
                    id=lease_id,
                    resource_id=candidate.id,
                    acquired_by=principal_id,
                    consumer=request.consumer,
                    acquired_at=acquired_at,
                    expires_at=expires_at,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    metadata_=request.metadata,
                )
                .returning(*Lease.__table__.c)
            )
        )
        .mappings()
        .one()
    )
    await connection.execute(
        update(Resource).where(Resource.id == candidate.id).values(last_leased_at=acquired_at)
    )
    await record_audit_event(connection, principal_id, "lease.acquired", "Lease", lease_id)
    projected = dict(row)
    projected.update(state="Active", duration_seconds=0.0, metadata=row["metadata"])
    projected.pop("idempotency_key")
    projected.pop("request_hash")
    projected.pop("resource_id")
    return LeaseResponse(resource=await fetch_resource(connection, candidate.id), **projected)


async def fetch_lease(
    connection: AsyncConnection,
    lease_id: uuid.UUID,
    principal_id: uuid.UUID,
    administrator: bool,
) -> LeaseResponse:
    """Return an authorized lease without disclosing another owner's history."""
    row = (
        (
            await connection.execute(
                lease_statement().where(
                    Lease.id == lease_id,
                    _authorized_lease_filter(principal_id, administrator),
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Lease not found")
    return await _lease_from_row(connection, row)


async def list_leases(
    connection: AsyncConnection,
    *,
    principal_id: uuid.UUID,
    administrator: bool,
    resource_id: uuid.UUID | None,
    state: str | None,
    acquired_by: uuid.UUID | None,
    consumer: str | None,
    acquired_after: datetime | None,
    acquired_before: datetime | None,
    expires_after: datetime | None,
    expires_before: datetime | None,
    offset: int,
    limit: int,
) -> LeasePage:
    """List an authorized deterministic page with initial practical filters."""
    filters: list[ColumnElement[bool]] = [_authorized_lease_filter(principal_id, administrator)]
    if resource_id is not None:
        filters.append(Lease.resource_id == resource_id)
    if state is not None:
        filters.append(_state_expression() == state)
    if acquired_by is not None:
        if not administrator and acquired_by != principal_id:
            return LeasePage(items=[], offset=offset, limit=limit, total=0)
        filters.append(Lease.acquired_by == acquired_by)
    if consumer is not None:
        filters.append(Lease.consumer == consumer)
    if acquired_after is not None:
        filters.append(Lease.acquired_at >= acquired_after)
    if acquired_before is not None:
        filters.append(Lease.acquired_at < acquired_before)
    if expires_after is not None:
        filters.append(Lease.expires_at >= expires_after)
    if expires_before is not None:
        filters.append(Lease.expires_at < expires_before)
    total = await connection.scalar(select(func.count()).select_from(Lease).where(*filters))
    rows = (
        (
            await connection.execute(
                lease_statement()
                .where(*filters)
                .order_by(Lease.acquired_at.desc(), Lease.id)
                .offset(offset)
                .limit(limit)
            )
        )
        .mappings()
        .all()
    )
    return LeasePage(
        items=[await _lease_from_row(connection, row) for row in rows],
        offset=offset,
        limit=limit,
        total=total or 0,
    )


async def _lock_lease_for_change(
    connection: AsyncConnection,
    lease_id: uuid.UUID,
    principal_id: uuid.UUID,
    owner_required: bool,
) -> RowMapping:
    """Lock a permitted lease and its resource in a consistent order."""
    filters: list[ColumnElement[bool]] = [Lease.id == lease_id]
    if owner_required:
        filters.append(Lease.acquired_by == principal_id)
    lease = (
        (await connection.execute(select(Lease.__table__).where(*filters).with_for_update()))
        .mappings()
        .one_or_none()
    )
    if lease is None:
        raise HTTPException(status_code=404, detail="Lease not found")
    await connection.execute(
        select(Resource.id).where(Resource.id == lease.resource_id).with_for_update()
    )
    return lease


async def renew_lease(
    connection: AsyncConnection,
    lease_id: uuid.UUID,
    request: LeaseRenew,
    principal_id: uuid.UUID,
) -> LeaseResponse:
    """Renew an active lease using server time and the current resource policy."""
    lease = await _lock_lease_for_change(connection, lease_id, principal_id, owner_required=True)
    if lease.ended_at is not None or (
        lease.expires_at is not None and lease.expires_at <= datetime.now(UTC)
    ):
        raise HTTPException(status_code=409, detail="Only an active lease can be renewed")
    resource = (
        (
            await connection.execute(
                select(Resource.__table__).where(Resource.id == lease.resource_id)
            )
        )
        .mappings()
        .one()
    )
    now = datetime.now(UTC)
    ttl = _resolve_ttl(resource, request.ttl_seconds, "ttl_seconds" in request.model_fields_set)
    expires_at = now + timedelta(seconds=ttl) if ttl is not None else None
    await connection.execute(
        update(Lease).where(Lease.id == lease_id).values(expires_at=expires_at, last_renewed_at=now)
    )
    await record_audit_event(connection, principal_id, "lease.renewed", "Lease", lease_id)
    return await fetch_lease(connection, lease_id, principal_id, False)


async def end_lease(
    connection: AsyncConnection,
    lease_id: uuid.UUID,
    principal_id: uuid.UUID,
    reason: Literal["Released", "Revoked"],
) -> LeaseResponse:
    """Idempotently release or administratively revoke a lease."""
    lease = await _lock_lease_for_change(
        connection,
        lease_id,
        principal_id,
        owner_required=reason == "Released",
    )
    if lease.ended_at is None:
        await connection.execute(
            update(Lease).where(Lease.id == lease_id).values(ended_at=func.now(), end_reason=reason)
        )
        await record_audit_event(
            connection, principal_id, f"lease.{reason.lower()}", "Lease", lease_id
        )
    return await fetch_lease(connection, lease_id, principal_id, reason == "Revoked")


async def expire_due_leases(connection: AsyncConnection, batch_size: int = 100) -> int:
    """Expire a bounded locked batch while coordinating on each resource row."""
    ids = (
        (
            await connection.execute(
                select(Lease.id)
                .where(
                    Lease.ended_at.is_(None),
                    Lease.expires_at.is_not(None),
                    Lease.expires_at <= func.now(),
                )
                .order_by(Lease.expires_at, Lease.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    expired = 0
    for lease_id in ids:
        lease = (
            (await connection.execute(select(Lease.__table__).where(Lease.id == lease_id)))
            .mappings()
            .one()
        )
        await connection.execute(
            select(Resource.id).where(Resource.id == lease.resource_id).with_for_update()
        )
        changed = await connection.execute(
            update(Lease)
            .where(
                Lease.id == lease_id,
                Lease.ended_at.is_(None),
                Lease.expires_at <= func.now(),
            )
            .values(ended_at=Lease.expires_at, end_reason="Expired")
            .returning(Lease.id)
        )
        if changed.scalar_one_or_none() is not None:
            await record_audit_event(connection, None, "lease.expired", "Lease", lease_id)
            expired += 1
    return expired
