"""Specify atomic audit coverage for lease creation and lifecycle changes."""

from __future__ import annotations

import httpx
import pytest

from tests.integration.leasing.support import create_owned_lease, expire_lease, lease_audit_events
from tests.integration.support import IdentityEnvironment

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_acquisition_records_lease_acquired_audit_event(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Successful acquisition atomically records one lease.acquired event for its owner."""
    lease = await create_owned_lease(identity_client, identity_environment, "audit-acquire")
    events = await lease_audit_events(identity_environment, lease["id"], "lease.acquired")
    assert len(events) == 1
    assert events[0]["actor_id"] == identity_environment.user_id
    assert events[0]["subject_id"] == lease["id"]


async def test_renewal_records_lease_renewed_audit_event(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Successful owner renewal atomically records one lease.renewed event."""
    lease = await create_owned_lease(
        identity_client,
        identity_environment,
        "audit-renew",
        default_ttl_seconds=60,
        max_ttl_seconds=120,
    )
    response = await identity_client.post(
        f"/v1/leases/{lease['id']}/renew",
        headers=identity_environment.authorization("user"),
        json={"ttl_seconds": 90},
    )
    events = await lease_audit_events(identity_environment, lease["id"], "lease.renewed")
    assert response.status_code == 200
    assert len(events) == 1
    assert events[0]["actor_id"] == identity_environment.user_id


async def test_release_records_lease_released_audit_event(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Successful owner release atomically records one lease.released event."""
    lease = await create_owned_lease(identity_client, identity_environment, "audit-release")
    response = await identity_client.post(
        f"/v1/leases/{lease['id']}/release",
        headers=identity_environment.authorization("user"),
    )
    events = await lease_audit_events(identity_environment, lease["id"], "lease.released")
    assert response.status_code == 200
    assert len(events) == 1
    assert events[0]["actor_id"] == identity_environment.user_id


async def test_revoke_records_lease_revoked_audit_event(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Successful administrator revocation atomically records one lease.revoked event."""
    lease = await create_owned_lease(identity_client, identity_environment, "audit-revoke")
    response = await identity_client.post(
        f"/v1/leases/{lease['id']}/revoke",
        headers=identity_environment.authorization("admin"),
    )
    events = await lease_audit_events(identity_environment, lease["id"], "lease.revoked")
    assert response.status_code == 200
    assert len(events) == 1
    assert events[0]["actor_id"] == identity_environment.admin_id


async def test_expiration_records_lease_expired_audit_event(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Worker expiration atomically records one actorless lease.expired event."""
    lease = await create_owned_lease(
        identity_client, identity_environment, "audit-expire", ttl_seconds=60
    )
    await expire_lease(identity_environment, lease["id"])
    events = await lease_audit_events(identity_environment, lease["id"], "lease.expired")
    assert len(events) == 1
    assert events[0]["actor_id"] is None


async def test_repeated_release_does_not_duplicate_audit_event(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Idempotent repeated release does not append another lease.released event."""
    lease = await create_owned_lease(identity_client, identity_environment, "audit-release-twice")
    path = f"/v1/leases/{lease['id']}/release"
    await identity_client.post(path, headers=identity_environment.authorization("user"))
    await identity_client.post(path, headers=identity_environment.authorization("user"))
    events = await lease_audit_events(identity_environment, lease["id"], "lease.released")
    assert len(events) == 1


async def test_repeated_revoke_does_not_duplicate_audit_event(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Idempotent repeated revocation does not append another lease.revoked event."""
    lease = await create_owned_lease(identity_client, identity_environment, "audit-revoke-twice")
    path = f"/v1/leases/{lease['id']}/revoke"
    await identity_client.post(path, headers=identity_environment.authorization("admin"))
    await identity_client.post(path, headers=identity_environment.authorization("admin"))
    events = await lease_audit_events(identity_environment, lease["id"], "lease.revoked")
    assert len(events) == 1
