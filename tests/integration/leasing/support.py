"""Provide focused setup helpers shared by leasing integration tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

import httpx

from shepherd_rm.database import transaction
from shepherd_rm.leasing.database import leasing_transaction
from shepherd_rm.leasing.persistence import expire_due_leases
from tests.integration.support import IdentityEnvironment

TTL_OMITTED = object()


async def create_lease_resource(
    client: httpx.AsyncClient,
    environment: IdentityEnvironment,
    prefix: str,
    *,
    resource_type: str | None = None,
    sharing_mode: Literal["Exclusive", "Shared"] = "Exclusive",
    expiration_mode: Literal["Optional", "Required"] = "Optional",
    default_ttl_seconds: int | None = None,
    max_ttl_seconds: int | None = None,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a public resource with an explicitly controlled leasing policy."""
    response = await client.post(
        "/v1/resources",
        headers=environment.authorization("admin"),
        json={
            "name": environment.name(f"{prefix}-resource"),
            "type": resource_type or environment.name(f"{prefix}-type"),
            "sharing_mode": sharing_mode,
            "visibility_mode": "Public",
            "expiration_mode": expiration_mode,
            "default_ttl_seconds": default_ttl_seconds,
            "max_ttl_seconds": max_ttl_seconds,
            "labels": labels or {},
        },
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


async def acquire_test_lease(
    client: httpx.AsyncClient,
    environment: IdentityEnvironment,
    resource: dict[str, Any],
    prefix: str,
    *,
    headers: dict[str, str] | None = None,
    ttl_seconds: object = TTL_OMITTED,
    consumer: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> httpx.Response:
    """Acquire a test resource while preserving omitted versus explicit-null TTL intent."""
    request: dict[str, Any] = {
        "resource_type": resource["type"],
        "sharing_mode": resource["sharing_mode"],
        "consumer": consumer,
        "metadata": metadata or {},
    }
    if ttl_seconds is not TTL_OMITTED:
        request["ttl_seconds"] = ttl_seconds
    authorization = headers or environment.authorization("user")
    return await client.post(
        "/v1/leases",
        headers={**authorization, "Idempotency-Key": environment.name(f"{prefix}-key")},
        json=request,
    )


async def create_owned_lease(
    client: httpx.AsyncClient,
    environment: IdentityEnvironment,
    prefix: str,
    *,
    sharing_mode: Literal["Exclusive", "Shared"] = "Exclusive",
    expiration_mode: Literal["Optional", "Required"] | None = None,
    default_ttl_seconds: int | None = None,
    max_ttl_seconds: int | None = None,
    ttl_seconds: object = TTL_OMITTED,
    consumer: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a public exclusive resource and acquire it as the regular test user."""
    resource = await create_lease_resource(
        client,
        environment,
        prefix,
        sharing_mode=sharing_mode,
        expiration_mode=expiration_mode
        or ("Required" if default_ttl_seconds is not None else "Optional"),
        default_ttl_seconds=default_ttl_seconds,
        max_ttl_seconds=max_ttl_seconds,
    )
    acquired = await acquire_test_lease(
        client,
        environment,
        resource,
        prefix,
        ttl_seconds=ttl_seconds,
        consumer=consumer,
        metadata=metadata,
    )
    assert acquired.status_code == 201
    return cast(dict[str, Any], acquired.json())


async def make_lease_due(environment: IdentityEnvironment, lease_id: str) -> None:
    """Move a finite lease's valid timestamps into the past without sleeping."""
    acquired_at = datetime.now(UTC) - timedelta(seconds=2)
    expires_at = acquired_at + timedelta(seconds=1)
    async with transaction(environment.settings) as connection:
        await connection.execute(
            "UPDATE leases SET acquired_at = %s, expires_at = %s WHERE id = %s",
            (acquired_at, expires_at, uuid.UUID(lease_id)),
        )


async def expire_lease(environment: IdentityEnvironment, lease_id: str) -> None:
    """Make one finite lease due and run the production expiration worker."""
    await make_lease_due(environment, lease_id)
    async with leasing_transaction(environment.settings) as connection:
        await expire_due_leases(connection)


async def set_lease_times(
    environment: IdentityEnvironment,
    lease_id: str,
    *,
    acquired_at: datetime,
    expires_at: datetime | None = None,
) -> None:
    """Set deterministic timestamps for lease list boundary and ordering tests."""
    async with transaction(environment.settings) as connection:
        await connection.execute(
            "UPDATE leases SET acquired_at = %s, expires_at = %s WHERE id = %s",
            (acquired_at, expires_at, uuid.UUID(lease_id)),
        )


async def lease_audit_events(
    environment: IdentityEnvironment,
    lease_id: str,
    action: str,
) -> list[dict[str, Any]]:
    """Return audit rows for one lease and action in creation order."""
    async with transaction(environment.settings) as connection:
        cursor = await connection.execute(
            """
            SELECT actor_id, action, subject_type, subject_id, metadata
            FROM audit_events
            WHERE subject_type = 'Lease' AND subject_id = %s AND action = %s
            ORDER BY id
            """,
            (lease_id, action),
        )
        return [
            {
                "actor_id": row[0],
                "action": row[1],
                "subject_type": row[2],
                "subject_id": row[3],
                "metadata": row[4],
            }
            for row in await cursor.fetchall()
        ]
