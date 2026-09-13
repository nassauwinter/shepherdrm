"""Verify owner and administrator lease lifecycle transitions."""

from __future__ import annotations

import httpx
import pytest

from tests.integration.leasing.support import create_owned_lease, expire_lease
from tests.integration.support import IdentityEnvironment, create_test_resource

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_releasing_exclusive_lease_restores_acquisition(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Releasing an exclusive lease makes its resource available to another principal."""
    await create_test_resource(
        identity_client,
        identity_environment,
        "leasing-release",
        resource_type="release-flow",
        visibility_mode="Public",
    )
    request = {"resource_type": "release-flow", "sharing_mode": "Exclusive"}
    first = await identity_client.post(
        "/v1/leases",
        headers={**identity_environment.authorization("user"), "Idempotency-Key": "release-first"},
        json=request,
    )
    assert first.status_code == 201

    released = await identity_client.post(
        f"/v1/leases/{first.json()['id']}/release",
        headers=identity_environment.authorization("user"),
    )
    assert released.status_code == 200
    assert released.json()["state"] == "Released"
    second = await identity_client.post(
        "/v1/leases",
        headers={
            **identity_environment.authorization("admin"),
            "Idempotency-Key": "release-second",
        },
        json=request,
    )
    assert second.status_code == 201


async def test_administrator_cannot_renew_another_principals_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Administrative inspection does not grant owner-only renewal permission."""
    acquired = await create_owned_lease(identity_client, identity_environment, "admin-renew")
    lease_path = f"/v1/leases/{acquired['id']}"
    renewed = await identity_client.post(
        f"{lease_path}/renew",
        headers=identity_environment.authorization("admin"),
        json={},
    )
    assert renewed.status_code == 404


async def test_administrator_cannot_release_another_principals_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Administrative inspection does not grant owner-only release permission."""
    acquired = await create_owned_lease(identity_client, identity_environment, "admin-release")
    released = await identity_client.post(
        f"/v1/leases/{acquired['id']}/release",
        headers=identity_environment.authorization("admin"),
    )
    assert released.status_code == 404


async def test_administrator_can_revoke_another_principals_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Administrator revocation terminates another principal's active lease."""
    acquired = await create_owned_lease(identity_client, identity_environment, "admin-revoke")
    revoked = await identity_client.post(
        f"/v1/leases/{acquired['id']}/revoke",
        headers=identity_environment.authorization("admin"),
    )
    assert revoked.status_code == 200
    assert revoked.json()["state"] == "Revoked"


async def test_active_lease_can_be_renewed(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Owner renewal of an active lease succeeds and preserves its active state."""
    lease = await create_owned_lease(
        identity_client,
        identity_environment,
        "active-renew",
        default_ttl_seconds=60,
        max_ttl_seconds=120,
    )
    response = await identity_client.post(
        f"/v1/leases/{lease['id']}/renew",
        headers=identity_environment.authorization("user"),
        json={"ttl_seconds": 90},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "Active"
    assert response.json()["last_renewed_at"] is not None


async def test_release_is_idempotent(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Repeated owner release preserves the released state without another transition."""
    lease = await create_owned_lease(identity_client, identity_environment, "release-twice")
    path = f"/v1/leases/{lease['id']}/release"
    first = await identity_client.post(path, headers=identity_environment.authorization("user"))
    second = await identity_client.post(path, headers=identity_environment.authorization("user"))
    assert first.status_code == second.status_code == 200
    assert second.json()["state"] == "Released"
    assert second.json()["ended_at"] == first.json()["ended_at"]


async def test_revoke_is_idempotent(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Repeated administrator revocation preserves the original revoked terminal state."""
    lease = await create_owned_lease(identity_client, identity_environment, "revoke-twice")
    path = f"/v1/leases/{lease['id']}/revoke"
    first = await identity_client.post(path, headers=identity_environment.authorization("admin"))
    second = await identity_client.post(path, headers=identity_environment.authorization("admin"))
    assert first.status_code == second.status_code == 200
    assert second.json()["state"] == "Revoked"
    assert second.json()["ended_at"] == first.json()["ended_at"]


async def test_revoke_after_release_preserves_released_state(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Administrative revocation after owner release does not replace the Released outcome."""
    lease = await create_owned_lease(identity_client, identity_environment, "release-revoke")
    await identity_client.post(
        f"/v1/leases/{lease['id']}/release",
        headers=identity_environment.authorization("user"),
    )
    response = await identity_client.post(
        f"/v1/leases/{lease['id']}/revoke",
        headers=identity_environment.authorization("admin"),
    )
    assert response.status_code == 200
    assert response.json()["state"] == "Released"


async def test_release_after_revoke_preserves_revoked_state(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Owner release after administrative revocation does not replace the Revoked outcome."""
    lease = await create_owned_lease(identity_client, identity_environment, "revoke-release")
    await identity_client.post(
        f"/v1/leases/{lease['id']}/revoke",
        headers=identity_environment.authorization("admin"),
    )
    response = await identity_client.post(
        f"/v1/leases/{lease['id']}/release",
        headers=identity_environment.authorization("user"),
    )
    assert response.status_code == 200
    assert response.json()["state"] == "Revoked"


async def test_revoke_after_expiration_preserves_expired_state(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Administrative revocation after worker expiration preserves the Expired outcome."""
    lease = await create_owned_lease(
        identity_client, identity_environment, "expire-revoke", ttl_seconds=60
    )
    await expire_lease(identity_environment, lease["id"])
    response = await identity_client.post(
        f"/v1/leases/{lease['id']}/revoke",
        headers=identity_environment.authorization("admin"),
    )
    assert response.status_code == 200
    assert response.json()["state"] == "Expired"


async def test_release_after_expiration_preserves_expired_state(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Owner release after worker expiration preserves the Expired outcome."""
    lease = await create_owned_lease(
        identity_client, identity_environment, "expire-release", ttl_seconds=60
    )
    await expire_lease(identity_environment, lease["id"])
    response = await identity_client.post(
        f"/v1/leases/{lease['id']}/release",
        headers=identity_environment.authorization("user"),
    )
    assert response.status_code == 200
    assert response.json()["state"] == "Expired"


async def test_released_lease_cannot_be_renewed(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Renewing a released lease returns conflict and leaves its terminal state unchanged."""
    lease = await create_owned_lease(identity_client, identity_environment, "released-renew")
    await identity_client.post(
        f"/v1/leases/{lease['id']}/release",
        headers=identity_environment.authorization("user"),
    )
    response = await identity_client.post(
        f"/v1/leases/{lease['id']}/renew",
        headers=identity_environment.authorization("user"),
        json={},
    )
    assert response.status_code == 409


async def test_revoked_lease_cannot_be_renewed(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Renewing a revoked lease returns conflict and leaves its terminal state unchanged."""
    lease = await create_owned_lease(identity_client, identity_environment, "revoked-renew")
    await identity_client.post(
        f"/v1/leases/{lease['id']}/revoke",
        headers=identity_environment.authorization("admin"),
    )
    response = await identity_client.post(
        f"/v1/leases/{lease['id']}/renew",
        headers=identity_environment.authorization("user"),
        json={},
    )
    assert response.status_code == 409


async def test_expired_lease_cannot_be_renewed(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Renewing an expired lease returns conflict and leaves its terminal state unchanged."""
    lease = await create_owned_lease(
        identity_client, identity_environment, "expired-renew", ttl_seconds=60
    )
    await expire_lease(identity_environment, lease["id"])
    response = await identity_client.post(
        f"/v1/leases/{lease['id']}/renew",
        headers=identity_environment.authorization("user"),
        json={},
    )
    assert response.status_code == 409


async def test_revoking_exclusive_lease_restores_acquisition(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Revoking an exclusive lease makes its resource available to another principal."""
    await create_test_resource(
        identity_client,
        identity_environment,
        "revoke-restores",
        resource_type="revoke-restores",
        visibility_mode="Public",
    )
    request = {"resource_type": "revoke-restores", "sharing_mode": "Exclusive"}
    first = await identity_client.post(
        "/v1/leases",
        headers={**identity_environment.authorization("user"), "Idempotency-Key": "revoke-first"},
        json=request,
    )
    await identity_client.post(
        f"/v1/leases/{first.json()['id']}/revoke",
        headers=identity_environment.authorization("admin"),
    )
    second = await identity_client.post(
        "/v1/leases",
        headers={**identity_environment.authorization("admin"), "Idempotency-Key": "revoke-second"},
        json=request,
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["resource"]["id"] == first.json()["resource"]["id"]
