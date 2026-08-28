"""Verify acquisition and renewal behavior across resource expiration policies."""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from tests.integration.leasing.support import (
    acquire_test_lease,
    create_lease_resource,
    create_owned_lease,
)
from tests.integration.support import IdentityEnvironment

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_unrepresentable_ttl_is_rejected_by_contract(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """An acquisition TTL beyond the documented safety ceiling returns validation failure."""
    response = await identity_client.post(
        "/v1/leases",
        headers={**identity_environment.authorization("user"), "Idempotency-Key": "huge-ttl"},
        json={
            "resource_type": "environment",
            "sharing_mode": "Exclusive",
            "ttl_seconds": 10**20,
        },
    )
    assert response.status_code == 422


async def test_boolean_acquisition_ttl_is_rejected_by_contract(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Boolean acquisition TTL input is not coerced into an integer duration."""
    response = await identity_client.post(
        "/v1/leases",
        headers={**identity_environment.authorization("user"), "Idempotency-Key": "bool-ttl"},
        json={
            "resource_type": "environment",
            "sharing_mode": "Exclusive",
            "ttl_seconds": True,
        },
    )
    assert response.status_code == 422


async def test_boolean_resource_ttl_is_rejected_by_contract(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Boolean resource TTL policy input is not coerced into an integer duration."""
    response = await identity_client.post(
        "/v1/resources",
        headers=identity_environment.authorization("admin"),
        json={
            "name": identity_environment.name("boolean-resource-ttl"),
            "type": "account",
            "sharing_mode": "Exclusive",
            "default_ttl_seconds": True,
        },
    )
    assert response.status_code == 422


async def test_omitted_ttl_uses_required_resource_default(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Omitting acquisition TTL uses the selected required resource's default."""
    created = await identity_client.post(
        "/v1/resources",
        headers=identity_environment.authorization("admin"),
        json={
            "name": identity_environment.name("leasing-ttl"),
            "type": "account",
            "sharing_mode": "Exclusive",
            "visibility_mode": "Public",
            "expiration_mode": "Required",
            "default_ttl_seconds": 60,
            "max_ttl_seconds": 120,
        },
    )
    assert created.status_code == 201
    acquired = await identity_client.post(
        "/v1/leases",
        headers={**identity_environment.authorization("user"), "Idempotency-Key": "ttl"},
        json={"resource_type": "account", "sharing_mode": "Exclusive"},
    )
    assert acquired.status_code == 201
    acquired_at = datetime.fromisoformat(acquired.json()["acquired_at"])
    expires_at = datetime.fromisoformat(acquired.json()["expires_at"])
    assert 59 <= (expires_at - acquired_at).total_seconds() <= 60


async def test_renewal_above_resource_maximum_returns_conflict(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Owner renewal above the resource maximum is rejected without changing the lease."""
    acquired = await create_owned_lease(
        identity_client,
        identity_environment,
        "renewal-limit",
        default_ttl_seconds=60,
        max_ttl_seconds=120,
    )
    too_long = await identity_client.post(
        f"/v1/leases/{acquired['id']}/renew",
        headers=identity_environment.authorization("user"),
        json={"ttl_seconds": 121},
    )
    assert too_long.status_code == 409


async def test_successful_renewal_records_server_timestamp(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """An owner renewal to 90 seconds under a 120-second maximum records a timestamp."""
    acquired = await create_owned_lease(
        identity_client,
        identity_environment,
        "renewal-success",
        default_ttl_seconds=60,
        max_ttl_seconds=120,
    )
    renewed = await identity_client.post(
        f"/v1/leases/{acquired['id']}/renew",
        headers=identity_environment.authorization("user"),
        json={"ttl_seconds": 90},
    )
    assert renewed.status_code == 200
    assert renewed.json()["last_renewed_at"] is not None


async def test_optional_resource_without_default_creates_indefinite_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Omitting TTL for an optional resource without a default creates an indefinite lease."""
    lease = await create_owned_lease(identity_client, identity_environment, "optional-omitted")
    assert lease["state"] == "Active"
    assert lease["expires_at"] is None


async def test_explicit_null_requests_indefinite_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Explicit null TTL selects an optional resource and creates an indefinite lease."""
    resource = await create_lease_resource(
        identity_client,
        identity_environment,
        "optional-null",
        default_ttl_seconds=60,
    )
    response = await acquire_test_lease(
        identity_client,
        identity_environment,
        resource,
        "optional-null",
        ttl_seconds=None,
    )
    assert response.status_code == 201
    assert response.json()["expires_at"] is None


async def test_explicit_ttl_overrides_resource_default(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """An explicit acquisition TTL replaces the selected resource's configured default."""
    resource = await create_lease_resource(
        identity_client,
        identity_environment,
        "explicit-ttl",
        default_ttl_seconds=60,
        max_ttl_seconds=180,
    )
    response = await acquire_test_lease(
        identity_client,
        identity_environment,
        resource,
        "explicit-ttl",
        ttl_seconds=120,
    )
    assert response.status_code == 201
    acquired_at = datetime.fromisoformat(response.json()["acquired_at"])
    expires_at = datetime.fromisoformat(response.json()["expires_at"])
    assert (expires_at - acquired_at).total_seconds() == 120


async def test_renewal_without_ttl_uses_current_resource_default(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Omitting renewal TTL resolves expiration from the resource's current default policy."""
    resource = await create_lease_resource(
        identity_client,
        identity_environment,
        "current-default",
        default_ttl_seconds=60,
        max_ttl_seconds=180,
    )
    acquired = await acquire_test_lease(
        identity_client, identity_environment, resource, "current-default"
    )
    assert acquired.status_code == 201
    updated = await identity_client.patch(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("admin"),
        json={"version": resource["version"], "default_ttl_seconds": 120},
    )
    assert updated.status_code == 200
    renewed = await identity_client.post(
        f"/v1/leases/{acquired.json()['id']}/renew",
        headers=identity_environment.authorization("user"),
        json={},
    )
    assert renewed.status_code == 200
    renewed_at = datetime.fromisoformat(renewed.json()["last_renewed_at"])
    expires_at = datetime.fromisoformat(renewed.json()["expires_at"])
    assert (expires_at - renewed_at).total_seconds() == 120


async def test_optional_lease_can_be_renewed_from_indefinite_to_finite(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Explicit finite renewal gives an optional indefinite lease an expiration timestamp."""
    lease = await create_owned_lease(identity_client, identity_environment, "indefinite-finite")
    response = await identity_client.post(
        f"/v1/leases/{lease['id']}/renew",
        headers=identity_environment.authorization("user"),
        json={"ttl_seconds": 90},
    )
    assert response.status_code == 200
    assert response.json()["expires_at"] is not None


async def test_optional_lease_can_be_renewed_from_finite_to_indefinite(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Explicit null renewal removes expiration from a finite lease on an optional resource."""
    lease = await create_owned_lease(
        identity_client,
        identity_environment,
        "finite-indefinite",
        default_ttl_seconds=60,
        expiration_mode="Optional",
    )
    response = await identity_client.post(
        f"/v1/leases/{lease['id']}/renew",
        headers=identity_environment.authorization("user"),
        json={"ttl_seconds": None},
    )
    assert response.status_code == 200
    assert response.json()["expires_at"] is None


async def test_required_resource_rejects_explicit_indefinite_acquisition(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Explicit null acquisition cannot select a resource whose expiration policy is required."""
    resource = await create_lease_resource(
        identity_client,
        identity_environment,
        "required-null-acquire",
        expiration_mode="Required",
        default_ttl_seconds=60,
    )
    response = await acquire_test_lease(
        identity_client,
        identity_environment,
        resource,
        "required-null-acquire",
        ttl_seconds=None,
    )
    assert response.status_code == 409


async def test_required_resource_rejects_indefinite_renewal(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Explicit null renewal is rejected for a resource whose expiration policy is required."""
    lease = await create_owned_lease(
        identity_client,
        identity_environment,
        "required-null-renew",
        default_ttl_seconds=60,
    )
    response = await identity_client.post(
        f"/v1/leases/{lease['id']}/renew",
        headers=identity_environment.authorization("user"),
        json={"ttl_seconds": None},
    )
    assert response.status_code == 409
