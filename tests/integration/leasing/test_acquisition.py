"""Verify lease candidate selection, availability, visibility, and concurrency."""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from shepherd_rm.database import transaction
from tests.integration.leasing.support import acquire_test_lease, create_lease_resource
from tests.integration.support import IdentityEnvironment, create_test_resource

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_active_exclusive_lease_blocks_second_acquisition(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """An active exclusive lease prevents a second principal from acquiring its resource."""
    await create_test_resource(
        identity_client,
        identity_environment,
        "leasing-exclusive",
        visibility_mode="Public",
        labels={"region": "test"},
    )
    request = {
        "resource_type": "environment",
        "sharing_mode": "Exclusive",
        "labels": {"region": "test"},
        "ttl_seconds": None,
    }
    first = await identity_client.post(
        "/v1/leases",
        headers={**identity_environment.authorization("user"), "Idempotency-Key": "first"},
        json=request,
    )
    assert first.status_code == 201
    blocked = await identity_client.post(
        "/v1/leases",
        headers={**identity_environment.authorization("admin"), "Idempotency-Key": "second"},
        json=request,
    )
    assert blocked.status_code == 409


async def test_shared_resource_accepts_concurrent_leases(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Two principals can acquire independent active leases on one shared resource."""
    resource = await create_test_resource(
        identity_client,
        identity_environment,
        "leasing-shared",
        sharing_mode="Shared",
        visibility_mode="Public",
    )
    request = {"resource_type": "environment", "sharing_mode": "Shared", "labels": {}}

    async def acquire(actor: str, key: str) -> httpx.Response:
        headers = identity_environment.authorization("user" if actor == "user" else "admin")
        return await identity_client.post(
            "/v1/leases", headers={**headers, "Idempotency-Key": key}, json=request
        )

    user_response, admin_response = await asyncio.gather(
        acquire("user", "shared-user"), acquire("admin", "shared-admin")
    )
    assert user_response.status_code == 201
    assert admin_response.status_code == 201
    assert user_response.json()["resource"]["id"] == resource["id"]
    assert admin_response.json()["resource"]["id"] == resource["id"]
    assert user_response.json()["id"] != admin_response.json()["id"]


async def test_concurrent_exclusive_acquisition_creates_only_one_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Concurrent principals cannot both acquire the same exclusive resource."""
    await create_test_resource(
        identity_client,
        identity_environment,
        "leasing-exclusive-race",
        visibility_mode="Public",
        resource_type="exclusive-race",
    )
    request = {"resource_type": "exclusive-race", "sharing_mode": "Exclusive"}

    async def acquire(actor: str, key: str) -> httpx.Response:
        headers = identity_environment.authorization("user" if actor == "user" else "admin")
        return await identity_client.post(
            "/v1/leases", headers={**headers, "Idempotency-Key": key}, json=request
        )

    responses = await asyncio.gather(acquire("user", "race-user"), acquire("admin", "race-admin"))
    assert sorted(response.status_code for response in responses) == [201, 409]


async def test_acquisition_skips_resource_with_incompatible_ttl(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Candidate selection skips an earlier resource whose maximum is below the requested TTL."""
    first_id = uuid.UUID(int=uuid.uuid4().int & ~1)
    second_id = uuid.UUID(int=first_id.int + 1)
    async with transaction(identity_environment.settings) as connection:
        await connection.execute(
            """
            INSERT INTO resources
                (id, name, type, sharing_mode, visibility_mode, expiration_mode,
                 default_ttl_seconds, max_ttl_seconds)
            VALUES (%s, %s, 'ttl-selection', 'Exclusive', 'Public', 'Required', 60, 60),
                   (%s, %s, 'ttl-selection', 'Exclusive', 'Public', 'Required', 300, 300)
            """,
            (
                first_id,
                identity_environment.name("ttl-incompatible"),
                second_id,
                identity_environment.name("ttl-compatible"),
            ),
        )
    response = await identity_client.post(
        "/v1/leases",
        headers={**identity_environment.authorization("user"), "Idempotency-Key": "ttl-select"},
        json={
            "resource_type": "ttl-selection",
            "sharing_mode": "Exclusive",
            "ttl_seconds": 120,
        },
    )
    assert response.status_code == 201
    assert response.json()["resource"]["id"] == str(second_id)


async def test_administrator_can_acquire_restricted_resource(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Administrator acquisition bypasses regular-user catalog visibility grants."""
    resource = await create_test_resource(
        identity_client,
        identity_environment,
        "admin-restricted-lease",
        resource_type="admin-restricted",
    )
    response = await identity_client.post(
        "/v1/leases",
        headers={**identity_environment.authorization("admin"), "Idempotency-Key": "admin-view"},
        json={"resource_type": "admin-restricted", "sharing_mode": "Exclusive"},
    )
    assert response.status_code == 201
    assert response.json()["resource"]["id"] == resource["id"]


async def test_restricted_resources_require_catalog_visibility(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Lease candidate selection excludes a restricted resource until access is granted."""
    resource = await create_test_resource(
        identity_client, identity_environment, "leasing-restricted"
    )
    request = {"resource_type": "environment", "sharing_mode": "Exclusive"}
    headers = {**identity_environment.authorization("user"), "Idempotency-Key": "visibility"}
    hidden = await identity_client.post("/v1/leases", headers=headers, json=request)
    assert hidden.status_code == 409
    granted = await identity_client.put(
        f"/v1/resources/{resource['id']}/access/principals/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert granted.status_code == 204
    visible = await identity_client.post("/v1/leases", headers=headers, json=request)
    assert visible.status_code == 201


async def test_acquisition_returns_active_lease_representation(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Successful acquisition returns the selected resource and complete active lease fields."""
    resource = await create_lease_resource(
        identity_client,
        identity_environment,
        "complete-acquisition",
        default_ttl_seconds=60,
        max_ttl_seconds=120,
        labels={"region": "test"},
    )
    response = await acquire_test_lease(
        identity_client,
        identity_environment,
        resource,
        "complete-acquisition",
        ttl_seconds=90,
        consumer="smoke-runner",
        metadata={"build": 42},
    )

    assert response.status_code == 201
    lease = response.json()
    assert lease["resource"]["id"] == resource["id"]
    assert lease["resource"]["name"] == resource["name"]
    assert lease["resource"]["available"] is False
    assert lease["acquired_by"] == str(identity_environment.user_id)
    assert lease["consumer"] == "smoke-runner"
    assert lease["metadata"] == {"build": 42}
    assert lease["state"] == "Active"
    assert lease["expires_at"] is not None
    assert lease["last_renewed_at"] is None
    assert lease["ended_at"] is None
    assert lease["end_reason"] is None
