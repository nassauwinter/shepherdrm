"""Verify principal-scoped idempotency behavior for lease acquisition."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.integration.leasing.support import expire_lease
from tests.integration.support import IdentityEnvironment, create_test_resource

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_same_idempotency_key_and_request_returns_original_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Replaying the same acquisition request and key returns the original lease."""
    await create_test_resource(
        identity_client,
        identity_environment,
        "leasing-idempotent",
        visibility_mode="Public",
    )
    headers = {**identity_environment.authorization("user"), "Idempotency-Key": "stable-key"}
    request = {"resource_type": "environment", "sharing_mode": "Exclusive", "labels": {}}
    original = await identity_client.post("/v1/leases", headers=headers, json=request)
    replay = await identity_client.post("/v1/leases", headers=headers, json=request)
    assert original.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["id"] == original.json()["id"]


async def test_same_idempotency_key_with_changed_request_returns_conflict(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Reusing an acquisition key with a changed payload returns conflict."""
    await create_test_resource(
        identity_client,
        identity_environment,
        "leasing-idempotency-conflict",
        resource_type="idempotency-conflict",
        visibility_mode="Public",
    )
    headers = {**identity_environment.authorization("user"), "Idempotency-Key": "conflict-key"}
    request = {"resource_type": "idempotency-conflict", "sharing_mode": "Exclusive"}
    original = await identity_client.post("/v1/leases", headers=headers, json=request)
    assert original.status_code == 201
    conflict = await identity_client.post(
        "/v1/leases", headers=headers, json={**request, "consumer": "different"}
    )
    assert conflict.status_code == 409


async def test_same_idempotency_key_is_independent_between_principals(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Different principals can use the same key for independent acquisition requests."""
    await create_test_resource(
        identity_client,
        identity_environment,
        "principal-keys",
        resource_type="principal-keys",
        sharing_mode="Shared",
        visibility_mode="Public",
    )
    request = {"resource_type": "principal-keys", "sharing_mode": "Shared"}
    user = await identity_client.post(
        "/v1/leases",
        headers={**identity_environment.authorization("user"), "Idempotency-Key": "same-key"},
        json=request,
    )
    admin = await identity_client.post(
        "/v1/leases",
        headers={**identity_environment.authorization("admin"), "Idempotency-Key": "same-key"},
        json=request,
    )
    assert user.status_code == admin.status_code == 201
    assert user.json()["id"] != admin.json()["id"]


async def test_concurrent_replay_creates_one_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Concurrent identical requests from one principal and key resolve to one stored lease."""
    await create_test_resource(
        identity_client,
        identity_environment,
        "concurrent-replay",
        resource_type="concurrent-replay",
        visibility_mode="Public",
    )
    headers = {
        **identity_environment.authorization("user"),
        "Idempotency-Key": "concurrent-replay",
    }
    request = {"resource_type": "concurrent-replay", "sharing_mode": "Exclusive"}
    first, second = await asyncio.gather(
        identity_client.post("/v1/leases", headers=headers, json=request),
        identity_client.post("/v1/leases", headers=headers, json=request),
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


async def test_replay_after_release_returns_original_released_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Replaying an acquisition after release returns its original released lease."""
    await create_test_resource(
        identity_client,
        identity_environment,
        "release-replay",
        resource_type="release-replay",
        visibility_mode="Public",
    )
    headers = {**identity_environment.authorization("user"), "Idempotency-Key": "release-replay"}
    request = {"resource_type": "release-replay", "sharing_mode": "Exclusive"}
    original = await identity_client.post("/v1/leases", headers=headers, json=request)
    await identity_client.post(
        f"/v1/leases/{original.json()['id']}/release",
        headers=identity_environment.authorization("user"),
    )
    replay = await identity_client.post("/v1/leases", headers=headers, json=request)
    assert replay.status_code == 201
    assert replay.json()["id"] == original.json()["id"]
    assert replay.json()["state"] == "Released"


async def test_replay_after_expiration_returns_original_expired_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Replaying an acquisition after expiration returns its original expired lease."""
    await create_test_resource(
        identity_client,
        identity_environment,
        "expiration-replay",
        resource_type="expiration-replay",
        visibility_mode="Public",
    )
    headers = {
        **identity_environment.authorization("user"),
        "Idempotency-Key": "expiration-replay",
    }
    request = {
        "resource_type": "expiration-replay",
        "sharing_mode": "Exclusive",
        "ttl_seconds": 60,
    }
    original = await identity_client.post("/v1/leases", headers=headers, json=request)
    await expire_lease(identity_environment, original.json()["id"])
    replay = await identity_client.post("/v1/leases", headers=headers, json=request)
    assert replay.status_code == 201
    assert replay.json()["id"] == original.json()["id"]
    assert replay.json()["state"] == "Expired"
