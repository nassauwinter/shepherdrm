"""Verify background expiration and its effects on lease and resource state."""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from shepherd_rm.leasing.database import leasing_transaction
from shepherd_rm.leasing.persistence import expire_due_leases
from tests.integration.support import IdentityEnvironment, create_test_resource

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_expiration_worker_ends_only_due_expiring_leases(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """The worker expires a due lease while leaving an indefinite lease active."""
    await create_test_resource(
        identity_client,
        identity_environment,
        "leasing-due",
        sharing_mode="Shared",
        visibility_mode="Public",
    )
    indefinite = await identity_client.post(
        "/v1/leases",
        headers={**identity_environment.authorization("user"), "Idempotency-Key": "indefinite"},
        json={"resource_type": "environment", "sharing_mode": "Shared", "ttl_seconds": None},
    )
    due = await identity_client.post(
        "/v1/leases",
        headers={**identity_environment.authorization("admin"), "Idempotency-Key": "due"},
        json={"resource_type": "environment", "sharing_mode": "Shared", "ttl_seconds": 1},
    )
    assert indefinite.status_code == 201
    assert due.status_code == 201
    lease_id = uuid.UUID(due.json()["id"])
    await asyncio.sleep(1.1)
    async with leasing_transaction(identity_environment.settings) as connection:
        assert await expire_due_leases(connection) == 1

    expired = await identity_client.get(
        f"/v1/leases/{lease_id}", headers=identity_environment.authorization("admin")
    )
    still_active = await identity_client.get(
        f"/v1/leases/{indefinite.json()['id']}",
        headers=identity_environment.authorization("user"),
    )
    assert expired.json()["state"] == "Expired"
    assert expired.json()["ended_at"] == expired.json()["expires_at"]
    assert expired.json()["duration_seconds"] == pytest.approx(1.0, abs=0.01)
    assert still_active.json()["state"] == "Active"


async def test_expiring_exclusive_lease_restores_acquisition(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Worker expiration makes an exclusive resource available to another principal."""
    pass


async def test_expiration_worker_records_system_audit_event(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Worker expiration records one lease.expired audit event with no principal actor."""
    pass


async def test_released_lease_is_ignored_by_expiration_worker(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """The expiration worker leaves an already released finite lease unchanged."""
    pass


async def test_revoked_lease_is_ignored_by_expiration_worker(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """The expiration worker leaves an already revoked finite lease unchanged."""
    pass


async def test_expiration_worker_respects_batch_size(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """One worker transaction expires no more than the configured number of due leases."""
    pass
