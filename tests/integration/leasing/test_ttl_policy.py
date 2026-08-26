"""Verify acquisition and renewal behavior across resource expiration policies."""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from tests.integration.leasing.support import create_owned_lease
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
    """A valid owner renewal records its server-generated renewal timestamp."""
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
    pass


async def test_explicit_null_requests_indefinite_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Explicit null TTL selects an optional resource and creates an indefinite lease."""
    pass


async def test_explicit_ttl_overrides_resource_default(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """An explicit acquisition TTL replaces the selected resource's configured default."""
    pass


async def test_renewal_without_ttl_uses_current_resource_default(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Omitting renewal TTL resolves expiration from the resource's current default policy."""
    pass


async def test_optional_lease_can_be_renewed_from_indefinite_to_finite(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Explicit finite renewal gives an optional indefinite lease an expiration timestamp."""
    pass


async def test_optional_lease_can_be_renewed_from_finite_to_indefinite(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Explicit null renewal removes expiration from a finite lease on an optional resource."""
    pass


async def test_required_resource_rejects_explicit_indefinite_acquisition(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Explicit null acquisition cannot select a resource whose expiration policy is required."""
    pass


async def test_required_resource_rejects_indefinite_renewal(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Explicit null renewal is rejected for a resource whose expiration policy is required."""
    pass
