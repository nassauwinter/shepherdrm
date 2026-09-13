"""Specify retrieval, listing, authorization, filtering, and pagination coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

import httpx
import pytest

from tests.integration.leasing.support import (
    acquire_test_lease,
    create_lease_resource,
    create_owned_lease,
    expire_lease,
    set_lease_times,
)
from tests.integration.support import IdentityEnvironment, create_authenticated_test_user

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_owner_can_get_own_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """A lease owner can retrieve the complete representation of their own lease."""
    lease = await create_owned_lease(identity_client, identity_environment, "owner-get")
    response = await identity_client.get(
        f"/v1/leases/{lease['id']}", headers=identity_environment.authorization("user")
    )
    assert response.status_code == 200
    assert response.json()["id"] == lease["id"]
    assert response.json()["acquired_by"] == str(identity_environment.user_id)


async def test_administrator_can_get_another_principals_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """An administrator can inspect a lease acquired by another principal."""
    lease = await create_owned_lease(identity_client, identity_environment, "admin-get")
    response = await identity_client.get(
        f"/v1/leases/{lease['id']}", headers=identity_environment.authorization("admin")
    )
    assert response.status_code == 200
    assert response.json()["id"] == lease["id"]


async def test_other_principal_cannot_get_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """A regular principal receives not found when retrieving another owner's lease."""
    lease = await create_owned_lease(identity_client, identity_environment, "other-get")
    other = await create_authenticated_test_user(
        identity_client, identity_environment, "other-get-user"
    )
    response = await identity_client.get(f"/v1/leases/{lease['id']}", headers=other.headers)
    assert response.status_code == 404


async def test_get_returns_complete_consumer_metadata_and_resource(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Lease retrieval returns consumer, metadata, timestamps, state, and selected resource."""
    lease = await create_owned_lease(
        identity_client,
        identity_environment,
        "complete-get",
        ttl_seconds=90,
        consumer="deployment-runner",
        metadata={"ticket": "OPS-42"},
    )
    response = await identity_client.get(
        f"/v1/leases/{lease['id']}", headers=identity_environment.authorization("user")
    )
    body = response.json()
    assert response.status_code == 200
    assert body["consumer"] == "deployment-runner"
    assert body["metadata"] == {"ticket": "OPS-42"}
    assert body["resource"]["id"] == lease["resource"]["id"]
    assert body["acquired_at"] is not None
    assert body["expires_at"] is not None
    assert body["state"] == "Active"


async def test_owner_can_get_released_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """A lease owner retains retrieval access to their released lease history."""
    lease = await create_owned_lease(identity_client, identity_environment, "released-get")
    await identity_client.post(
        f"/v1/leases/{lease['id']}/release",
        headers=identity_environment.authorization("user"),
    )
    response = await identity_client.get(
        f"/v1/leases/{lease['id']}", headers=identity_environment.authorization("user")
    )
    assert response.status_code == 200
    assert response.json()["state"] == "Released"


async def test_owner_can_get_expired_lease(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """A lease owner retains retrieval access to their expired lease history."""
    lease = await create_owned_lease(
        identity_client, identity_environment, "expired-get", ttl_seconds=60
    )
    await expire_lease(identity_environment, lease["id"])
    response = await identity_client.get(
        f"/v1/leases/{lease['id']}", headers=identity_environment.authorization("user")
    )
    assert response.status_code == 200
    assert response.json()["state"] == "Expired"


async def test_user_list_contains_only_owned_leases(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """A regular user's lease list excludes leases acquired by other principals."""
    resource = await create_lease_resource(
        identity_client, identity_environment, "user-list", sharing_mode="Shared"
    )
    own = await acquire_test_lease(identity_client, identity_environment, resource, "user-list-own")
    other = await acquire_test_lease(
        identity_client,
        identity_environment,
        resource,
        "user-list-admin",
        headers=identity_environment.authorization("admin"),
    )
    response = await identity_client.get(
        "/v1/leases", headers=identity_environment.authorization("user")
    )
    ids = {item["id"] for item in response.json()["items"]}
    assert own.json()["id"] in ids
    assert other.json()["id"] not in ids


async def test_administrator_list_contains_all_leases(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """An administrator's lease list includes leases from every principal."""
    resource = await create_lease_resource(
        identity_client, identity_environment, "admin-list", sharing_mode="Shared"
    )
    user_lease = await acquire_test_lease(
        identity_client, identity_environment, resource, "admin-list-user"
    )
    admin_lease = await acquire_test_lease(
        identity_client,
        identity_environment,
        resource,
        "admin-list-admin",
        headers=identity_environment.authorization("admin"),
    )
    response = await identity_client.get(
        "/v1/leases", headers=identity_environment.authorization("admin")
    )
    ids = {item["id"] for item in response.json()["items"]}
    assert {user_lease.json()["id"], admin_lease.json()["id"]} <= ids


async def test_regular_user_filtering_by_other_owner_returns_empty_page(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Filtering by another owner does not disclose lease existence to a regular user."""
    lease = await create_owned_lease(identity_client, identity_environment, "other-owner-filter")
    response = await identity_client.get(
        "/v1/leases",
        headers=identity_environment.authorization("user"),
        params={"acquired_by": str(identity_environment.admin_id)},
    )
    assert lease["id"] not in {item["id"] for item in response.json()["items"]}
    assert response.json()["total"] == 0


@pytest.mark.parametrize("state", ["Active", "Released", "Revoked", "Expired"])
async def test_lease_list_filters_by_state(
    identity_client: httpx.AsyncClient,
    identity_environment: IdentityEnvironment,
    state: Literal["Active", "Released", "Revoked", "Expired"],
) -> None:
    """The state filter returns only authorized leases in the requested lifecycle state."""
    lease = await create_owned_lease(
        identity_client,
        identity_environment,
        f"state-{state.lower()}",
        ttl_seconds=60 if state == "Expired" else None,
    )
    if state == "Released":
        await identity_client.post(
            f"/v1/leases/{lease['id']}/release",
            headers=identity_environment.authorization("user"),
        )
    elif state == "Revoked":
        await identity_client.post(
            f"/v1/leases/{lease['id']}/revoke",
            headers=identity_environment.authorization("admin"),
        )
    elif state == "Expired":
        await expire_lease(identity_environment, lease["id"])
    response = await identity_client.get(
        "/v1/leases",
        headers=identity_environment.authorization("user"),
        params={"state": state},
    )
    assert lease["id"] in {item["id"] for item in response.json()["items"]}
    assert all(item["state"] == state for item in response.json()["items"])


async def test_lease_list_filters_by_resource(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """The resource filter returns only authorized leases for the requested resource."""
    first = await create_owned_lease(identity_client, identity_environment, "resource-filter-one")
    second = await create_owned_lease(identity_client, identity_environment, "resource-filter-two")
    response = await identity_client.get(
        "/v1/leases",
        headers=identity_environment.authorization("user"),
        params={"resource_id": first["resource"]["id"]},
    )
    ids = {item["id"] for item in response.json()["items"]}
    assert first["id"] in ids
    assert second["id"] not in ids


async def test_lease_list_filters_by_exact_consumer(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """The consumer filter returns only leases with the exact requested consumer value."""
    resource = await create_lease_resource(
        identity_client, identity_environment, "consumer-filter", sharing_mode="Shared"
    )
    exact = await acquire_test_lease(
        identity_client,
        identity_environment,
        resource,
        "consumer-exact",
        consumer="runner",
    )
    different = await acquire_test_lease(
        identity_client,
        identity_environment,
        resource,
        "consumer-different",
        consumer="runner-child",
    )
    response = await identity_client.get(
        "/v1/leases",
        headers=identity_environment.authorization("user"),
        params={"consumer": "runner"},
    )
    ids = {item["id"] for item in response.json()["items"]}
    assert exact.json()["id"] in ids
    assert different.json()["id"] not in ids


async def test_lease_list_filters_by_acquisition_time_boundaries(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Acquisition-time filters apply inclusive lower and exclusive upper boundaries."""
    resource = await create_lease_resource(
        identity_client, identity_environment, "acquired-boundary", sharing_mode="Shared"
    )
    leases = [
        await acquire_test_lease(
            identity_client, identity_environment, resource, f"acquired-boundary-{index}"
        )
        for index in range(3)
    ]
    boundary = datetime(2026, 1, 2, tzinfo=UTC)
    times = [boundary - timedelta(seconds=1), boundary, boundary + timedelta(seconds=1)]
    for response, acquired_at in zip(leases, times, strict=True):
        await set_lease_times(identity_environment, response.json()["id"], acquired_at=acquired_at)
    response = await identity_client.get(
        "/v1/leases",
        headers=identity_environment.authorization("user"),
        params={
            "acquired_after": boundary.isoformat(),
            "acquired_before": (boundary + timedelta(seconds=1)).isoformat(),
        },
    )
    assert [item["id"] for item in response.json()["items"]] == [leases[1].json()["id"]]


async def test_expiration_time_filters_exclude_indefinite_leases(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Expiration-time filters match finite timestamps without including indefinite leases."""
    resource = await create_lease_resource(
        identity_client, identity_environment, "expiration-filter", sharing_mode="Shared"
    )
    finite = await acquire_test_lease(
        identity_client,
        identity_environment,
        resource,
        "expiration-filter-finite",
        ttl_seconds=120,
    )
    indefinite = await acquire_test_lease(
        identity_client,
        identity_environment,
        resource,
        "expiration-filter-indefinite",
        ttl_seconds=None,
    )
    expires_at = datetime.fromisoformat(finite.json()["expires_at"])
    response = await identity_client.get(
        "/v1/leases",
        headers=identity_environment.authorization("user"),
        params={
            "expires_after": expires_at.isoformat(),
            "expires_before": (expires_at + timedelta(seconds=1)).isoformat(),
        },
    )
    ids = {item["id"] for item in response.json()["items"]}
    assert finite.json()["id"] in ids
    assert indefinite.json()["id"] not in ids


async def test_lease_list_reports_total_before_pagination(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Lease listing reports the full authorized match count before applying page bounds."""
    resource = await create_lease_resource(
        identity_client, identity_environment, "total", sharing_mode="Shared"
    )
    for index in range(3):
        response = await acquire_test_lease(
            identity_client, identity_environment, resource, f"total-{index}"
        )
        assert response.status_code == 201
    page = await identity_client.get(
        "/v1/leases",
        headers=identity_environment.authorization("user"),
        params={"resource_id": resource["id"], "limit": 1},
    )
    assert len(page.json()["items"]) == 1
    assert page.json()["total"] == 3


async def test_lease_list_uses_stable_pagination_order(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Adjacent lease pages follow deterministic acquisition-time and identifier ordering."""
    resource = await create_lease_resource(
        identity_client, identity_environment, "stable-order", sharing_mode="Shared"
    )
    responses = [
        await acquire_test_lease(
            identity_client, identity_environment, resource, f"stable-order-{index}"
        )
        for index in range(3)
    ]
    acquired_at = datetime(2026, 1, 2, tzinfo=UTC)
    ids = [response.json()["id"] for response in responses]
    for lease_id in ids:
        await set_lease_times(identity_environment, lease_id, acquired_at=acquired_at)
    pages: list[dict[str, Any]] = []
    for offset in range(3):
        response = await identity_client.get(
            "/v1/leases",
            headers=identity_environment.authorization("user"),
            params={"resource_id": resource["id"], "offset": offset, "limit": 1},
        )
        pages.append(cast(dict[str, Any], response.json()))
    assert [page["items"][0]["id"] for page in pages] == sorted(ids)
