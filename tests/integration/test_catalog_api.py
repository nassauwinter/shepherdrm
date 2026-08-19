"""Exercise resource catalog behavior against authoritative PostgreSQL state."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
import pytest

from shepherd_rm.catalog.database import catalog_transaction
from shepherd_rm.catalog.models import ResourceCreate
from shepherd_rm.catalog.persistence import create_resource
from shepherd_rm.database import transaction
from tests.integration.support import (
    IdentityEnvironment,
    create_authenticated_test_user,
    create_test_group,
    create_test_resource,
    grant_resource_with_backend_pid,
    transition_resource_with_backend_pid,
    wait_until_backend_waits_for_lock,
)


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("method", "path", "actor", "expected_status"),
    [
        ("get", "/v1/resources", "anonymous", 401),
        ("post", "/v1/resources", "user", 403),
    ],
    ids=["discovery-requires-authentication", "writes-require-admin"],
)
async def test_resource_access_obeys_catalog_permissions(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
    method: Literal["get", "post"],
    path: str,
    actor: Literal["anonymous", "user"],
    expected_status: int,
) -> None:
    """Anonymous discovery and regular-user administration are rejected."""
    headers = {} if actor == "anonymous" else identity_environment.authorization("user")
    response = await identity_client.request(
        method,
        path,
        headers=headers,
        json={
            "name": identity_environment.name("forbidden-resource"),
            "type": "account",
            "sharing_mode": "Exclusive",
        }
        if method == "post"
        else None,
    )

    assert response.status_code == expected_status


@pytest.mark.anyio
@pytest.mark.integration
async def test_administrator_can_create_a_resource(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Creation returns the stored metadata and initial derived resource state."""
    response = await identity_client.post(
        "/v1/resources",
        headers=identity_environment.authorization("admin"),
        json={
            "name": identity_environment.name("created-resource"),
            "type": "android-device",
            "sharing_mode": "Exclusive",
            "labels": {"region": "eu"},
        },
    )

    assert response.status_code == 201
    assert response.json()["visibility_mode"] == "Restricted"
    assert response.json()["available"] is True
    assert response.json()["active_lease_count"] == 0


@pytest.mark.anyio
@pytest.mark.integration
async def test_resource_update_increments_its_version(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """A metadata change is stored and advances the optimistic version once."""
    resource = await create_test_resource(identity_client, identity_environment, "updated-resource")

    response = await identity_client.patch(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("admin"),
        json={"version": resource["version"], "labels": {"model": "tablet"}},
    )

    assert response.status_code == 200
    assert response.json()["version"] == resource["version"] + 1
    assert response.json()["labels"] == {"model": "tablet"}


@pytest.mark.anyio
@pytest.mark.integration
async def test_disabling_a_resource_makes_it_unavailable(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Disabling an active resource changes its status and derived availability."""
    resource = await create_test_resource(
        identity_client, identity_environment, "disabled-resource"
    )

    response = await identity_client.post(
        f"/v1/resources/{resource['id']}/disable",
        headers=identity_environment.authorization("admin"),
    )

    assert response.status_code == 200
    assert response.json()["operational_status"] == "Disabled"
    assert response.json()["available"] is False


@pytest.mark.anyio
@pytest.mark.integration
async def test_enabling_a_disabled_resource_restores_availability(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Enabling a disabled resource returns it to active and available state."""
    resource = await create_test_resource(identity_client, identity_environment, "enabled-resource")
    disabled = await identity_client.post(
        f"/v1/resources/{resource['id']}/disable",
        headers=identity_environment.authorization("admin"),
    )
    assert disabled.status_code == 200

    response = await identity_client.post(
        f"/v1/resources/{resource['id']}/enable",
        headers=identity_environment.authorization("admin"),
    )

    assert response.status_code == 200
    assert response.json()["operational_status"] == "Active"
    assert response.json()["available"] is True


@pytest.mark.anyio
@pytest.mark.integration
async def test_quarantining_a_resource_makes_it_unavailable(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Quarantining an active resource removes it from allocatable state."""
    resource = await create_test_resource(
        identity_client, identity_environment, "quarantined-resource"
    )

    response = await identity_client.post(
        f"/v1/resources/{resource['id']}/quarantine",
        headers=identity_environment.authorization("admin"),
    )

    assert response.status_code == 200
    assert response.json()["operational_status"] == "Quarantined"
    assert response.json()["available"] is False


@pytest.mark.anyio
@pytest.mark.integration
async def test_recovering_a_quarantined_resource_restores_availability(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Recovering a quarantined resource returns it to active and available state."""
    resource = await create_test_resource(
        identity_client, identity_environment, "recovered-resource"
    )
    quarantined = await identity_client.post(
        f"/v1/resources/{resource['id']}/quarantine",
        headers=identity_environment.authorization("admin"),
    )
    assert quarantined.status_code == 200

    response = await identity_client.post(
        f"/v1/resources/{resource['id']}/recover",
        headers=identity_environment.authorization("admin"),
    )

    assert response.status_code == 200
    assert response.json()["operational_status"] == "Active"
    assert response.json()["available"] is True


@pytest.mark.anyio
@pytest.mark.integration
async def test_archived_resource_is_excluded_from_normal_listing(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Normal catalog discovery omits an archived resource."""
    resource = await create_test_resource(identity_client, identity_environment, "normally-hidden")
    archived = await identity_client.delete(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("admin"),
    )
    assert archived.status_code == 204

    response = await identity_client.get(
        "/v1/resources", headers=identity_environment.authorization("admin")
    )

    assert response.status_code == 200
    assert resource["id"] not in {item["id"] for item in response.json()["items"]}


@pytest.mark.anyio
@pytest.mark.integration
async def test_administrator_can_include_archived_resources(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Administrator discovery can explicitly include an archived resource."""
    resource = await create_test_resource(identity_client, identity_environment, "included-archive")
    archived = await identity_client.delete(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("admin"),
    )
    assert archived.status_code == 204

    response = await identity_client.get(
        "/v1/resources?include_archived=true",
        headers=identity_environment.authorization("admin"),
    )

    assert response.status_code == 200
    assert resource["id"] in {item["id"] for item in response.json()["items"]}


@pytest.mark.anyio
@pytest.mark.integration
async def test_restricted_resource_is_hidden_from_ungranted_principal(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Direct retrieval does not disclose a restricted resource without a grant."""
    resource = await create_test_resource(identity_client, identity_environment, "ungranted-fetch")

    response = await identity_client.get(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("user"),
    )

    assert response.status_code == 404


@pytest.mark.anyio
@pytest.mark.integration
async def test_restricted_resource_is_excluded_from_ungranted_listing(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Catalog discovery omits a restricted resource without a grant."""
    resource = await create_test_resource(identity_client, identity_environment, "ungranted-list")

    response = await identity_client.get(
        "/v1/resources", headers=identity_environment.authorization("user")
    )

    assert response.status_code == 200
    assert resource["id"] not in {item["id"] for item in response.json()["items"]}


@pytest.mark.anyio
@pytest.mark.integration
async def test_direct_grant_exposes_resource_only_to_target_principal(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """A direct grant reveals a restricted resource only to its target principal."""
    other_user = await create_authenticated_test_user(
        identity_client, identity_environment, "direct-grant-other"
    )
    resource = await create_test_resource(identity_client, identity_environment, "direct-grant")
    granted = await identity_client.put(
        f"/v1/resources/{resource['id']}/access/principals/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert granted.status_code == 204

    target_response = await identity_client.get(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("user"),
    )
    other_response = await identity_client.get(
        f"/v1/resources/{resource['id']}", headers=other_user.headers
    )

    assert target_response.status_code == 200
    assert other_response.status_code == 404


@pytest.mark.anyio
@pytest.mark.integration
async def test_direct_grant_includes_resource_in_target_principal_listing(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """A directly granted resource appears in the target principal's catalog page."""
    resource = await create_test_resource(identity_client, identity_environment, "direct-list")
    granted = await identity_client.put(
        f"/v1/resources/{resource['id']}/access/principals/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert granted.status_code == 204

    response = await identity_client.get(
        "/v1/resources", headers=identity_environment.authorization("user")
    )

    assert response.status_code == 200
    assert resource["id"] in {item["id"] for item in response.json()["items"]}


@pytest.mark.anyio
@pytest.mark.integration
async def test_administrator_can_inspect_resource_grants(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Grant inspection returns the resource's direct principal and group targets."""
    resource = await create_test_resource(identity_client, identity_environment, "inspect-grants")
    granted = await identity_client.put(
        f"/v1/resources/{resource['id']}/access/principals/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert granted.status_code == 204

    response = await identity_client.get(
        f"/v1/resources/{resource['id']}/access",
        headers=identity_environment.authorization("admin"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "principal_ids": [str(identity_environment.user_id)],
        "group_ids": [],
    }


@pytest.mark.anyio
@pytest.mark.integration
async def test_regular_user_cannot_inspect_resource_grants(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Grant inspection requires administrator permission."""
    resource = await create_test_resource(identity_client, identity_environment, "forbidden-grants")

    response = await identity_client.get(
        f"/v1/resources/{resource['id']}/access",
        headers=identity_environment.authorization("user"),
    )

    assert response.status_code == 403


@pytest.mark.anyio
@pytest.mark.integration
async def test_revoking_direct_grant_removes_visibility(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Revoking a direct grant immediately hides the resource from its former target."""
    resource = await create_test_resource(identity_client, identity_environment, "revoked-grant")
    granted = await identity_client.put(
        f"/v1/resources/{resource['id']}/access/principals/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert granted.status_code == 204

    revoked = await identity_client.delete(
        f"/v1/resources/{resource['id']}/access/principals/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert revoked.status_code == 204
    response = await identity_client.get(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("user"),
    )

    assert response.status_code == 404


@pytest.mark.anyio
@pytest.mark.integration
async def test_direct_grant_changes_record_targeted_audit_events(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Direct grant and revocation events identify the affected principal."""
    resource = await create_test_resource(identity_client, identity_environment, "audited-direct")
    granted = await identity_client.put(
        f"/v1/resources/{resource['id']}/access/principals/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert granted.status_code == 204
    revoked = await identity_client.delete(
        f"/v1/resources/{resource['id']}/access/principals/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert revoked.status_code == 204

    async with transaction(identity_environment.settings) as connection:
        cursor = await connection.execute(
            """
            SELECT action, metadata
            FROM audit_events
            WHERE subject_type = 'Resource'
              AND subject_id = %s
              AND action IN ('resource.access.granted', 'resource.access.revoked')
            ORDER BY id
            """,
            (resource["id"],),
        )
        access_events = await cursor.fetchall()

    expected_metadata = {
        "target_type": "Principal",
        "target_id": str(identity_environment.user_id),
    }
    assert access_events == [
        ("resource.access.granted", expected_metadata),
        ("resource.access.revoked", expected_metadata),
    ]


@pytest.mark.anyio
@pytest.mark.integration
async def test_group_grant_exposes_resource_to_active_member(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """A restricted resource is visible through active membership in a granted group."""
    group_id = await create_test_group(identity_client, identity_environment, "member-group")
    membership = await identity_client.put(
        f"/v1/groups/{group_id}/members/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert membership.status_code == 204
    resource = await create_test_resource(identity_client, identity_environment, "member-resource")
    grant = await identity_client.put(
        f"/v1/resources/{resource['id']}/access/groups/{group_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert grant.status_code == 204

    response = await identity_client.get(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("user"),
    )

    assert response.status_code == 200


@pytest.mark.anyio
@pytest.mark.integration
async def test_group_grant_does_not_expose_resource_to_nonmember(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """A group grant does not reveal its resource to a principal outside the group."""
    group_id = await create_test_group(identity_client, identity_environment, "nonmember-group")
    resource = await create_test_resource(
        identity_client, identity_environment, "nonmember-resource"
    )
    grant = await identity_client.put(
        f"/v1/resources/{resource['id']}/access/groups/{group_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert grant.status_code == 204

    response = await identity_client.get(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("user"),
    )

    assert response.status_code == 404


@pytest.mark.anyio
@pytest.mark.integration
async def test_removing_group_membership_removes_resource_visibility(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Removing a member immediately ends visibility inherited from its group."""
    group_id = await create_test_group(identity_client, identity_environment, "removed-member")
    membership = await identity_client.put(
        f"/v1/groups/{group_id}/members/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert membership.status_code == 204
    resource = await create_test_resource(identity_client, identity_environment, "removed-resource")
    grant = await identity_client.put(
        f"/v1/resources/{resource['id']}/access/groups/{group_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert grant.status_code == 204
    visible = await identity_client.get(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("user"),
    )
    assert visible.status_code == 200

    removed = await identity_client.delete(
        f"/v1/groups/{group_id}/members/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert removed.status_code == 204
    response = await identity_client.get(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("user"),
    )

    assert response.status_code == 404


@pytest.mark.anyio
@pytest.mark.integration
async def test_archived_group_no_longer_provides_resource_visibility(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Archiving a granted group immediately stops its members inheriting access."""
    group_id = await create_test_group(identity_client, identity_environment, "archived-group")
    membership = await identity_client.put(
        f"/v1/groups/{group_id}/members/{identity_environment.user_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert membership.status_code == 204
    resource = await create_test_resource(
        identity_client, identity_environment, "archived-group-resource"
    )
    grant = await identity_client.put(
        f"/v1/resources/{resource['id']}/access/groups/{group_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert grant.status_code == 204
    visible = await identity_client.get(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("user"),
    )
    assert visible.status_code == 200

    archived = await identity_client.delete(
        f"/v1/groups/{group_id}", headers=identity_environment.authorization("admin")
    )
    assert archived.status_code == 204
    response = await identity_client.get(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("user"),
    )

    assert response.status_code == 404


@pytest.mark.anyio
@pytest.mark.integration
async def test_group_grant_audit_event_identifies_target_group(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """A group grant audit event identifies the affected group."""
    group_id = await create_test_group(identity_client, identity_environment, "audited-group")
    resource = await create_test_resource(
        identity_client, identity_environment, "audited-group-resource"
    )
    grant = await identity_client.put(
        f"/v1/resources/{resource['id']}/access/groups/{group_id}",
        headers=identity_environment.authorization("admin"),
    )
    assert grant.status_code == 204

    async with transaction(identity_environment.settings) as connection:
        cursor = await connection.execute(
            """
            SELECT metadata
            FROM audit_events
            WHERE subject_type = 'Resource'
              AND subject_id = %s
              AND action = 'resource.access.granted'
            """,
            (resource["id"],),
        )
        audit_metadata = await cursor.fetchone()

    assert audit_metadata == ({"target_type": "Group", "target_id": str(group_id)},)


@pytest.mark.anyio
@pytest.mark.integration
async def test_public_resource_is_visible_without_grants(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Every authenticated user can retrieve a resource explicitly marked public."""
    resource = await create_test_resource(
        identity_client,
        identity_environment,
        "public-resource",
        resource_type="device",
        visibility_mode="Public",
    )

    response = await identity_client.get(
        f"/v1/resources/{resource['id']}",
        headers=identity_environment.authorization("user"),
    )

    assert response.status_code == 200
    assert response.json()["visibility_mode"] == "Public"


@pytest.mark.anyio
@pytest.mark.integration
async def test_resource_list_combines_type_mode_and_label_filters(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Discovery applies exact type, sharing mode, and label criteria together."""
    resource_type = identity_environment.name("filtered-type")
    expected = []
    for prefix in ("filter-a", "filter-b"):
        expected.append(
            await create_test_resource(
                identity_client,
                identity_environment,
                prefix,
                resource_type=resource_type,
                sharing_mode="Shared",
                visibility_mode="Public",
                labels={"region": "eu"},
            )
        )
    await create_test_resource(
        identity_client,
        identity_environment,
        "filter-excluded",
        resource_type=resource_type,
        sharing_mode="Exclusive",
        visibility_mode="Public",
        labels={"region": "us"},
    )

    response = await identity_client.get(
        "/v1/resources",
        headers=identity_environment.authorization("user"),
        params=[
            ("type", resource_type),
            ("sharing_mode", "Shared"),
            ("label", "region=eu"),
        ],
    )

    assert response.status_code == 200
    assert {item["id"] for item in response.json()["items"]} == {
        resource["id"] for resource in expected
    }


@pytest.mark.anyio
@pytest.mark.integration
async def test_resource_list_reports_total_before_pagination(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """A limited page reports the total number of matching visible resources."""
    resource_type = identity_environment.name("total-type")
    for prefix in ("total-a", "total-b"):
        await create_test_resource(
            identity_client,
            identity_environment,
            prefix,
            resource_type=resource_type,
            visibility_mode="Public",
        )

    response = await identity_client.get(
        "/v1/resources",
        headers=identity_environment.authorization("user"),
        params={"type": resource_type, "limit": 1},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert len(response.json()["items"]) == 1


@pytest.mark.anyio
@pytest.mark.integration
async def test_resource_list_uses_stable_pagination_order(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Adjacent catalog pages follow deterministic resource-name ordering."""
    resource_type = identity_environment.name("ordered-type")
    first = await create_test_resource(
        identity_client,
        identity_environment,
        "ordered-a",
        resource_type=resource_type,
        visibility_mode="Public",
    )
    second = await create_test_resource(
        identity_client,
        identity_environment,
        "ordered-b",
        resource_type=resource_type,
        visibility_mode="Public",
    )

    first_page = await identity_client.get(
        "/v1/resources",
        headers=identity_environment.authorization("user"),
        params={"type": resource_type, "offset": 0, "limit": 1},
    )
    assert first_page.status_code == 200
    second_page = await identity_client.get(
        "/v1/resources",
        headers=identity_environment.authorization("user"),
        params={"type": resource_type, "offset": 1, "limit": 1},
    )

    assert second_page.status_code == 200
    assert first_page.json()["items"][0]["id"] == first["id"]
    assert second_page.json()["items"][0]["id"] == second["id"]


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize("field", ["name", "type", "sharing_mode", "visibility_mode", "labels"])
async def test_resource_updates_reject_explicit_null_fields(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
    field: str,
) -> None:
    """PATCH rejects null metadata fields that the OpenAPI contract defines as non-nullable."""
    headers = identity_environment.authorization("admin")
    created = await identity_client.post(
        "/v1/resources",
        headers=headers,
        json={
            "name": identity_environment.name(f"null-{field}"),
            "type": "environment",
            "sharing_mode": "Exclusive",
        },
    )
    assert created.status_code == 201

    response = await identity_client.patch(
        f"/v1/resources/{created.json()['id']}",
        headers=headers,
        json={"version": created.json()["version"], field: None},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.anyio
@pytest.mark.integration
async def test_resource_updates_reject_stale_versions(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """A stale optimistic version cannot overwrite a newer catalog update."""
    headers = identity_environment.authorization("admin")
    created = await identity_client.post(
        "/v1/resources",
        headers=headers,
        json={
            "name": identity_environment.name("versioned-resource"),
            "type": "environment",
            "sharing_mode": "Exclusive",
        },
    )
    assert created.status_code == 201
    resource = created.json()
    first = await identity_client.patch(
        f"/v1/resources/{resource['id']}",
        headers=headers,
        json={"version": resource["version"], "type": "updated-environment"},
    )
    assert first.status_code == 200

    stale = await identity_client.patch(
        f"/v1/resources/{resource['id']}",
        headers=headers,
        json={"version": resource["version"], "type": "lost-update"},
    )

    assert stale.status_code == 409


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize("sharing_mode", ["Exclusive", "Shared"])
async def test_availability_is_derived_from_mode_state_and_active_leases(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
    sharing_mode: Literal["Exclusive", "Shared"],
) -> None:
    """An active lease consumes exclusives while shared resources remain available."""
    resource_id = uuid.uuid4()
    async with transaction(identity_environment.settings) as connection:
        await connection.execute(
            """
            INSERT INTO resources (id, name, type, sharing_mode, visibility_mode)
            VALUES (%s, %s, 'account', %s, 'Public')
            """,
            (
                resource_id,
                identity_environment.name(f"leased-{sharing_mode.lower()}"),
                sharing_mode,
            ),
        )
        await connection.execute(
            """
            INSERT INTO leases
                (id, resource_id, acquired_by, expires_at, idempotency_key, request_hash)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                uuid.uuid4(),
                resource_id,
                identity_environment.user_id,
                datetime.now(UTC) + timedelta(hours=1),
                identity_environment.name(f"lease-{sharing_mode.lower()}"),
                "0" * 64,
            ),
        )

    response = await identity_client.get(
        f"/v1/resources/{resource_id}", headers=identity_environment.authorization("user")
    )

    assert response.status_code == 200
    assert response.json()["active_lease_count"] == 1
    assert response.json()["available"] is (sharing_mode == "Shared")


@pytest.mark.anyio
@pytest.mark.integration
async def test_active_lease_prevents_resource_archival(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Archival is rejected while a locked resource still has an active lease."""
    resource_id = uuid.uuid4()
    async with transaction(identity_environment.settings) as connection:
        await connection.execute(
            """
            INSERT INTO resources (id, name, type, sharing_mode)
            VALUES (%s, %s, 'account', 'Shared')
            """,
            (resource_id, identity_environment.name("archive-guard")),
        )
        await connection.execute(
            """
            INSERT INTO leases
                (id, resource_id, acquired_by, expires_at, idempotency_key, request_hash)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                uuid.uuid4(),
                resource_id,
                identity_environment.user_id,
                datetime.now(UTC) + timedelta(hours=1),
                identity_environment.name("archive-lease"),
                "1" * 64,
            ),
        )

    response = await identity_client.delete(
        f"/v1/resources/{resource_id}", headers=identity_environment.authorization("admin")
    )

    assert response.status_code == 409


@pytest.mark.anyio
@pytest.mark.integration
async def test_catalog_write_and_audit_event_are_atomic(
    identity_environment: IdentityEnvironment,
) -> None:
    """Rolling back a resource creation also rolls back its audit event."""
    subject_id: uuid.UUID | None = None
    with pytest.raises(RuntimeError, match="force catalog rollback"):
        async with catalog_transaction(identity_environment.settings) as connection:
            resource = await create_resource(
                connection,
                ResourceCreate(
                    name=identity_environment.name("rolled-back-resource"),
                    type="environment",
                    sharing_mode="Exclusive",
                ),
                identity_environment.admin_id,
            )
            subject_id = resource.id
            raise RuntimeError("force catalog rollback")

    assert subject_id is not None
    async with transaction(identity_environment.settings) as connection:
        row = await (
            await connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM resources WHERE id = %s),
                    (SELECT COUNT(*) FROM audit_events
                     WHERE subject_type = 'Resource' AND subject_id = %s)
                """,
                (subject_id, str(subject_id)),
            )
        ).fetchone()
    assert row == (0, 0)


@pytest.mark.anyio
@pytest.mark.integration
async def test_operational_transition_waits_for_the_resource_row_lock(
    identity_environment: IdentityEnvironment,
) -> None:
    """A state transition serializes behind another transaction holding the resource row."""
    resource_id = uuid.uuid4()
    async with transaction(identity_environment.settings) as setup:
        await setup.execute(
            """
            INSERT INTO resources (id, name, type, sharing_mode)
            VALUES (%s, %s, 'environment', 'Exclusive')
            """,
            (resource_id, identity_environment.name("locked-transition")),
        )

    loop = asyncio.get_running_loop()
    backend_pid: asyncio.Future[int] = loop.create_future()
    async with transaction(identity_environment.settings) as blocker:
        await blocker.execute("SELECT id FROM resources WHERE id = %s FOR UPDATE", (resource_id,))
        transition = asyncio.create_task(
            transition_resource_with_backend_pid(
                identity_environment.settings,
                resource_id,
                identity_environment.admin_id,
                backend_pid,
            )
        )
        await wait_until_backend_waits_for_lock(
            identity_environment.settings.database_url, await backend_pid
        )
        assert not transition.done()

    result = await transition

    assert result.operational_status == "Quarantined"


@pytest.mark.anyio
@pytest.mark.integration
async def test_access_grant_waits_for_the_resource_row_lock(
    identity_environment: IdentityEnvironment,
) -> None:
    """Changing resource visibility grants serializes on the affected resource row."""
    resource_id = uuid.uuid4()
    async with transaction(identity_environment.settings) as setup:
        await setup.execute(
            """
            INSERT INTO resources (id, name, type, sharing_mode)
            VALUES (%s, %s, 'environment', 'Exclusive')
            """,
            (resource_id, identity_environment.name("locked-grant")),
        )

    loop = asyncio.get_running_loop()
    backend_pid: asyncio.Future[int] = loop.create_future()
    async with transaction(identity_environment.settings) as blocker:
        await blocker.execute("SELECT id FROM resources WHERE id = %s FOR UPDATE", (resource_id,))
        grant = asyncio.create_task(
            grant_resource_with_backend_pid(
                identity_environment.settings,
                resource_id,
                identity_environment.user_id,
                identity_environment.admin_id,
                backend_pid,
            )
        )
        await wait_until_backend_waits_for_lock(
            identity_environment.settings.database_url, await backend_pid
        )
        assert not grant.done()

    await grant
    async with transaction(identity_environment.settings) as connection:
        row = await (
            await connection.execute(
                """
                SELECT principal_id
                FROM resource_principal_grants
                WHERE resource_id = %s
                """,
                (resource_id,),
            )
        ).fetchone()
    assert row == (identity_environment.user_id,)
