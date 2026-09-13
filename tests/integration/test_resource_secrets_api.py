"""Exercise resource-secret management, disclosure, authorization, and auditing."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import text

from shepherd_rm.catalog.database import catalog_transaction
from shepherd_rm.catalog.persistence import set_resource_grant
from shepherd_rm.database import transaction
from shepherd_rm.leasing.database import leasing_transaction
from shepherd_rm.leasing.persistence import end_lease
from shepherd_rm.resource_secrets.crypto import SecretCipher
from shepherd_rm.resource_secrets.database import resource_secret_transaction
from shepherd_rm.resource_secrets.persistence import access_lease_secret
from tests.integration.leasing.support import acquire_test_lease, create_lease_resource
from tests.integration.support import (
    IdentityEnvironment,
    create_authenticated_test_user,
    create_test_group,
    wait_until_backend_waits_for_lock,
)

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def create_secret(
    client: httpx.AsyncClient,
    environment: IdentityEnvironment,
    resource_id: str,
    *,
    name: str,
    material: dict[str, str],
) -> dict[str, Any]:
    """Create one test-owned resource secret and return its safe descriptor."""
    response = await client.post(
        f"/v1/resources/{resource_id}/secrets",
        headers=environment.authorization("admin"),
        json={"name": name, "description": "Test credential", "material": material},
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


async def assert_user_cannot_use_lease_secrets(
    client: httpx.AsyncClient,
    environment: IdentityEnvironment,
    lease_id: str,
    secret_id: str,
) -> None:
    """Assert both discovery and disclosure hide a lease after visibility is revoked."""
    listed = await client.get(
        f"/v1/leases/{lease_id}/secrets",
        headers=environment.authorization("user"),
    )
    accessed = await client.post(
        f"/v1/leases/{lease_id}/secrets/{secret_id}/access",
        headers=environment.authorization("user"),
    )

    assert listed.status_code == 404
    assert accessed.status_code == 404


async def test_administration_keeps_managed_material_out_of_metadata_responses(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Create, list, and detail responses never expose a managed plaintext value."""
    resource = await create_lease_resource(identity_client, identity_environment, "secret-meta")
    value = "managed-administrator-credential"
    secret = await create_secret(
        identity_client,
        identity_environment,
        resource["id"],
        name="device-login",
        material={"mode": "Managed", "value": value},
    )

    listed = await identity_client.get(
        f"/v1/resources/{resource['id']}/secrets",
        headers=identity_environment.authorization("admin"),
    )
    detailed = await identity_client.get(
        f"/v1/resources/{resource['id']}/secrets/{secret['id']}",
        headers=identity_environment.authorization("admin"),
    )

    assert listed.status_code == 200
    assert detailed.status_code == 200
    assert value not in listed.text
    assert value not in detailed.text
    assert listed.json()[0]["mode"] == "Managed"
    assert "value" not in detailed.json()
    assert "reference" not in detailed.json()


async def test_explicit_administrator_access_decrypts_and_audits_managed_material(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """An administrator can explicitly reveal a managed value without storing it in audit data."""
    resource = await create_lease_resource(identity_client, identity_environment, "secret-admin")
    value = "managed-secret-value"
    secret = await create_secret(
        identity_client,
        identity_environment,
        resource["id"],
        name="administrator-access",
        material={"mode": "Managed", "value": value},
    )

    response = await identity_client.post(
        f"/v1/resources/{resource['id']}/secrets/{secret['id']}/access",
        headers=identity_environment.authorization("admin"),
    )
    async with transaction(identity_environment.settings) as connection:
        stored = await (
            await connection.execute(
                "SELECT encrypted_value, external_reference FROM resource_secrets WHERE id = %s",
                (uuid.UUID(secret["id"]),),
            )
        ).fetchone()
        audit = await (
            await connection.execute(
                "SELECT metadata::text FROM audit_events "
                "WHERE subject_type = 'ResourceSecret' AND subject_id = %s "
                "AND action = 'resource_secret.accessed'",
                (secret["id"],),
            )
        ).fetchone()

    assert response.status_code == 200
    assert response.json()["value"] == value
    assert response.headers["cache-control"] == "no-store"
    assert stored is not None and value.encode() not in bytes(stored[0])
    assert stored[1] is None
    assert audit is not None and value not in audit[0]


async def test_external_reference_is_returned_only_by_explicit_access(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """External references remain absent from metadata and available through explicit access."""
    resource = await create_lease_resource(identity_client, identity_environment, "secret-external")
    reference = "qa/devices/device-17"
    secret = await create_secret(
        identity_client,
        identity_environment,
        resource["id"],
        name="vault-login",
        material={"mode": "External", "provider": "vault", "reference": reference},
    )

    response = await identity_client.post(
        f"/v1/resources/{resource['id']}/secrets/{secret['id']}/access",
        headers=identity_environment.authorization("admin"),
    )

    assert reference not in str(secret)
    assert response.status_code == 200
    assert response.json() == {
        "id": secret["id"],
        "name": "vault-login",
        "mode": "External",
        "provider": "vault",
        "reference": reference,
    }


async def test_material_replacement_can_change_mode_with_version_control(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """A complete replacement can change storage mode while stale updates return conflict."""
    resource = await create_lease_resource(identity_client, identity_environment, "secret-update")
    secret = await create_secret(
        identity_client,
        identity_environment,
        resource["id"],
        name="replaceable",
        material={"mode": "Managed", "value": "old-value"},
    )
    path = f"/v1/resources/{resource['id']}/secrets/{secret['id']}"

    updated = await identity_client.patch(
        path,
        headers=identity_environment.authorization("admin"),
        json={
            "version": secret["version"],
            "material": {"mode": "External", "provider": "vault", "reference": "new/path"},
        },
    )
    stale = await identity_client.patch(
        path,
        headers=identity_environment.authorization("admin"),
        json={"version": secret["version"], "description": "stale"},
    )

    assert updated.status_code == 200
    assert updated.json()["mode"] == "External"
    assert updated.json()["version"] == secret["version"] + 1
    assert stale.status_code == 409


async def test_lease_owner_discovers_and_accesses_secret_but_another_user_cannot(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Only an active lease owner or administrator can discover and reveal leased secrets."""
    resource = await create_lease_resource(identity_client, identity_environment, "secret-lease")
    secret = await create_secret(
        identity_client,
        identity_environment,
        resource["id"],
        name="leased-login",
        material={"mode": "Managed", "value": "lease-only-value"},
    )
    acquired = await acquire_test_lease(
        identity_client, identity_environment, resource, "secret-lease"
    )
    assert acquired.status_code == 201
    lease = acquired.json()
    other = await create_authenticated_test_user(
        identity_client, identity_environment, "secret-other-user"
    )

    listed = await identity_client.get(
        f"/v1/leases/{lease['id']}/secrets",
        headers=identity_environment.authorization("user"),
    )
    accessed = await identity_client.post(
        f"/v1/leases/{lease['id']}/secrets/{secret['id']}/access",
        headers=identity_environment.authorization("user"),
    )
    denied = await identity_client.post(
        f"/v1/leases/{lease['id']}/secrets/{secret['id']}/access",
        headers=other.headers,
    )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [secret["id"]]
    assert "value" not in listed.json()[0]
    assert accessed.status_code == 200
    assert accessed.json()["value"] == "lease-only-value"
    assert denied.status_code == 404


async def test_ended_or_overdue_lease_cannot_access_secret_material(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Release and database-time expiry immediately remove lease-based secret access."""
    resource = await create_lease_resource(identity_client, identity_environment, "secret-ended")
    secret = await create_secret(
        identity_client,
        identity_environment,
        resource["id"],
        name="ended-login",
        material={"mode": "Managed", "value": "ended-value"},
    )
    acquired = await acquire_test_lease(
        identity_client, identity_environment, resource, "secret-ended", ttl_seconds=60
    )
    assert acquired.status_code == 201
    lease_id = acquired.json()["id"]
    path = f"/v1/leases/{lease_id}/secrets/{secret['id']}/access"

    released = await identity_client.post(
        f"/v1/leases/{lease_id}/release",
        headers=identity_environment.authorization("user"),
    )
    after_release = await identity_client.post(
        path, headers=identity_environment.authorization("user")
    )
    assert released.status_code == 200
    assert after_release.status_code == 404

    second = await acquire_test_lease(
        identity_client, identity_environment, resource, "secret-overdue", ttl_seconds=60
    )
    assert second.status_code == 201
    overdue_id = second.json()["id"]
    async with transaction(identity_environment.settings) as connection:
        acquired_at = datetime.now(UTC) - timedelta(seconds=2)
        await connection.execute(
            "UPDATE leases SET acquired_at = %s, expires_at = %s WHERE id = %s",
            (acquired_at, acquired_at + timedelta(seconds=1), uuid.UUID(overdue_id)),
        )
    overdue = await identity_client.post(
        f"/v1/leases/{overdue_id}/secrets/{secret['id']}/access",
        headers=identity_environment.authorization("user"),
    )
    assert overdue.status_code == 404


async def test_secret_access_serializes_with_concurrent_lease_release(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """A release waits for an authorized disclosure transaction before ending its lease."""
    resource = await create_lease_resource(
        identity_client, identity_environment, "secret-access-release"
    )
    secret = await create_secret(
        identity_client,
        identity_environment,
        resource["id"],
        name="serialized-login",
        material={"mode": "Managed", "value": "serialized-value"},
    )
    acquired = await acquire_test_lease(
        identity_client, identity_environment, resource, "secret-access-release"
    )
    assert acquired.status_code == 201
    lease_id = uuid.UUID(acquired.json()["id"])
    release_backend: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def release_with_backend() -> None:
        """Publish the releasing connection before it attempts to lock the lease."""
        async with leasing_transaction(identity_environment.settings) as connection:
            backend_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
            assert backend_pid is not None
            release_backend.set_result(int(backend_pid))
            await end_lease(connection, lease_id, identity_environment.user_id, "Released")

    async with resource_secret_transaction(identity_environment.settings) as connection:
        await access_lease_secret(
            connection,
            SecretCipher(identity_environment.settings),
            lease_id,
            uuid.UUID(secret["id"]),
            identity_environment.user_id,
            False,
        )
        release_task = asyncio.create_task(release_with_backend())
        backend_pid = await release_backend
        await wait_until_backend_waits_for_lock(
            identity_environment.settings.database_url, backend_pid
        )
        assert not release_task.done()

    await release_task
    denied = await identity_client.post(
        f"/v1/leases/{lease_id}/secrets/{secret['id']}/access",
        headers=identity_environment.authorization("user"),
    )
    assert denied.status_code == 404


async def test_public_visibility_revocation_removes_lease_secret_access(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Changing a leased public resource to restricted immediately removes owner access."""
    resource = await create_lease_resource(
        identity_client, identity_environment, "secret-public-revocation"
    )
    secret = await create_secret(
        identity_client,
        identity_environment,
        resource["id"],
        name="public-revocation",
        material={"mode": "Managed", "value": "public-revoked-value"},
    )
    acquired = await acquire_test_lease(
        identity_client, identity_environment, resource, "secret-public-revocation"
    )
    assert acquired.status_code == 201
    lease_id = acquired.json()["id"]

    restricted = await identity_client.patch(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("admin"),
        json={"version": resource["version"], "visibility_mode": "Restricted"},
    )
    assert restricted.status_code == 200

    await assert_user_cannot_use_lease_secrets(
        identity_client, identity_environment, lease_id, secret["id"]
    )
    admin_access = await identity_client.post(
        f"/v1/leases/{lease_id}/secrets/{secret['id']}/access",
        headers=identity_environment.authorization("admin"),
    )
    assert admin_access.status_code == 200


async def test_direct_grant_revocation_removes_lease_secret_access(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Removing the owner's direct grant immediately removes lease-secret access."""
    resource = await create_lease_resource(
        identity_client, identity_environment, "secret-direct-revocation"
    )
    restricted = await identity_client.patch(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("admin"),
        json={"version": resource["version"], "visibility_mode": "Restricted"},
    )
    assert restricted.status_code == 200
    granted = await identity_client.put(
        f"/v1/resources/{resource['id']}/access/principals/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert granted.status_code == 204
    secret = await create_secret(
        identity_client,
        identity_environment,
        resource["id"],
        name="direct-revocation",
        material={"mode": "Managed", "value": "direct-revoked-value"},
    )
    acquired = await acquire_test_lease(
        identity_client, identity_environment, resource, "secret-direct-revocation"
    )
    assert acquired.status_code == 201

    revoked = await identity_client.delete(
        f"/v1/resources/{resource['id']}/access/principals/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert revoked.status_code == 204
    await assert_user_cannot_use_lease_secrets(
        identity_client,
        identity_environment,
        acquired.json()["id"],
        secret["id"],
    )


async def test_secret_access_rechecks_visibility_after_waiting_for_grant_revocation(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Access waiting on an uncommitted direct-grant revocation observes its commit."""
    resource = await create_lease_resource(
        identity_client, identity_environment, "secret-concurrent-revocation"
    )
    restricted = await identity_client.patch(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("admin"),
        json={"version": resource["version"], "visibility_mode": "Restricted"},
    )
    assert restricted.status_code == 200
    granted = await identity_client.put(
        f"/v1/resources/{resource['id']}/access/principals/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert granted.status_code == 204
    secret = await create_secret(
        identity_client,
        identity_environment,
        resource["id"],
        name="concurrent-revocation",
        material={"mode": "Managed", "value": "must-not-be-revealed"},
    )
    acquired = await acquire_test_lease(
        identity_client, identity_environment, resource, "secret-concurrent-revocation"
    )
    assert acquired.status_code == 201
    resource_id = uuid.UUID(resource["id"])
    lease_id = uuid.UUID(acquired.json()["id"])
    secret_id = uuid.UUID(secret["id"])
    access_backend: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def access_while_revocation_is_pending() -> int:
        """Publish the accessor backend before it waits for the resource lock."""
        async with resource_secret_transaction(identity_environment.settings) as connection:
            backend_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
            assert backend_pid is not None
            access_backend.set_result(int(backend_pid))
            try:
                await access_lease_secret(
                    connection,
                    SecretCipher(identity_environment.settings),
                    lease_id,
                    secret_id,
                    identity_environment.user_id,
                    False,
                )
            except HTTPException as error:
                return error.status_code
            return 200

    async with catalog_transaction(identity_environment.settings) as connection:
        await set_resource_grant(
            connection,
            resource_id,
            identity_environment.user_id,
            "Principal",
            False,
            identity_environment.admin_id,
        )
        access_task = asyncio.create_task(access_while_revocation_is_pending())
        backend_pid = await access_backend
        await wait_until_backend_waits_for_lock(
            identity_environment.settings.database_url, backend_pid
        )
        assert not access_task.done()

    assert await access_task == 404


async def test_group_grant_revocation_removes_lease_secret_access(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """Removing the owner's group grant immediately removes lease-secret access."""
    resource = await create_lease_resource(
        identity_client, identity_environment, "secret-group-revocation"
    )
    restricted = await identity_client.patch(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("admin"),
        json={"version": resource["version"], "visibility_mode": "Restricted"},
    )
    assert restricted.status_code == 200
    group_id = await create_test_group(identity_client, identity_environment, "secret-access-group")
    member = await identity_client.put(
        f"/v1/groups/{group_id}/members/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert member.status_code == 204
    granted = await identity_client.put(
        f"/v1/resources/{resource['id']}/access/groups/{group_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert granted.status_code == 204
    secret = await create_secret(
        identity_client,
        identity_environment,
        resource["id"],
        name="group-revocation",
        material={"mode": "Managed", "value": "group-revoked-value"},
    )
    acquired = await acquire_test_lease(
        identity_client, identity_environment, resource, "secret-group-revocation"
    )
    assert acquired.status_code == 201

    revoked = await identity_client.delete(
        f"/v1/resources/{resource['id']}/access/groups/{group_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert revoked.status_code == 204
    await assert_user_cannot_use_lease_secrets(
        identity_client,
        identity_environment,
        acquired.json()["id"],
        secret["id"],
    )


async def test_secret_administration_requires_an_administrator(
    identity_client: httpx.AsyncClient, identity_environment: IdentityEnvironment
) -> None:
    """A regular user cannot list or create resource-secret metadata or material."""
    resource = await create_lease_resource(identity_client, identity_environment, "secret-authz")

    listed = await identity_client.get(
        f"/v1/resources/{resource['id']}/secrets",
        headers=identity_environment.authorization("user"),
    )
    created = await identity_client.post(
        f"/v1/resources/{resource['id']}/secrets",
        headers=identity_environment.authorization("user"),
        json={"name": "forbidden", "material": {"mode": "Managed", "value": "hidden"}},
    )

    assert listed.status_code == 403
    assert created.status_code == 403
