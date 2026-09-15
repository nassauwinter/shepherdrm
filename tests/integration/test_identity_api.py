"""Exercise isolated authentication and identity workflows against PostgreSQL."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config

from shepherd_rm.application import create_app
from shepherd_rm.config import Settings, get_settings
from shepherd_rm.database import transaction
from shepherd_rm.identity.authentication import (
    AuthenticationError,
    authenticate_token,
    issue_token,
    token_hash,
)
from shepherd_rm.identity.bootstrap import bootstrap_administrator
from shepherd_rm.identity.database import identity_transaction
from shepherd_rm.identity.models import PrincipalCreate
from shepherd_rm.identity.persistence import create_principal
from shepherd_rm.rate_limits import login_subject
from tests.integration.support import IdentityEnvironment, database_url_for_schema


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("actor", "expected_status"),
    [("anonymous", 401), ("user", 403)],
    ids=["authentication-required", "administrator-required"],
)
async def test_user_administration_requires_an_administrator(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
    actor: Literal["anonymous", "user"],
    expected_status: int,
) -> None:
    """Anonymous and regular-user requests cannot list managed users."""
    headers = {} if actor == "anonymous" else identity_environment.authorization("user")

    response = await identity_client.get("/v1/users", headers=headers)

    assert response.status_code == expected_status


@pytest.mark.anyio
@pytest.mark.integration
async def test_login_token_identifies_the_authenticated_user(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Credentials matching an active user issue a token that resolves to that user."""
    login_response = await identity_client.post(
        "/v1/auth/login",
        json={
            "username": identity_environment.name("user"),
            "password": "regular-user-password",
        },
    )
    assert login_response.status_code == 200
    access_token = login_response.json()["access_token"]

    me_response = await identity_client.get(
        "/v1/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert me_response.status_code == 200
    assert me_response.json()["id"] == str(identity_environment.user_id)


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize(
    "credential_state",
    ["invalid-password", "unknown-user", "archived-user"],
)
async def test_login_rejects_credentials_without_disclosing_account_state(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
    credential_state: Literal["invalid-password", "unknown-user", "archived-user"],
) -> None:
    """An incorrect password, unknown username, or archived user returns the same 401 problem."""
    username = identity_environment.name("user")
    password = "regular-user-password"
    if credential_state == "invalid-password":
        password = "incorrect-user-password"
    elif credential_state == "unknown-user":
        username = identity_environment.name("unknown-user")
    else:
        archived = await identity_client.delete(
            f"/v1/users/{identity_environment.user_id}",
            headers=identity_environment.authorization("admin"),
        )
        assert archived.status_code == 204

    response = await identity_client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"] == "Invalid username or password"


@pytest.mark.anyio
@pytest.mark.integration
async def test_login_rate_limit_is_shared_through_postgresql(
    identity_environment: IdentityEnvironment,
) -> None:
    """A second login in one-attempt window returns 429 with a retry interval."""
    username = identity_environment.name("user")
    subject_hash = login_subject(username)
    settings = identity_environment.settings.model_copy(
        update={"login_rate_limit_attempts": 1, "login_rate_limit_window_seconds": 300}
    )
    first_app = create_app(settings=settings)
    second_app = create_app(settings=settings)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app),
            base_url="http://test",
        ) as first_client:
            first = await first_client.post(
                "/v1/auth/login",
                json={"username": username, "password": "incorrect-password"},
            )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app),
            base_url="http://test",
        ) as second_client:
            limited = await second_client.post(
                "/v1/auth/login",
                json={"username": username, "password": "secret-canary"},
                headers={"x-correlation-id": "limited-login"},
            )
    finally:
        async with transaction(settings) as connection:
            await connection.execute(
                "DELETE FROM rate_limit_windows WHERE scope = %s AND subject_hash = %s",
                ("login", subject_hash),
            )

    assert first.status_code == 401
    assert limited.status_code == 429
    assert limited.headers["retry-after"].isdigit()
    assert limited.headers["x-correlation-id"] == "limited-login"
    assert "secret-canary" not in limited.text
    assert limited.json()["detail"] == "Too many login attempts"


@pytest.mark.anyio
@pytest.mark.integration
async def test_password_change_replaces_the_users_login_credential(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Changing a password rejects the old value and accepts the replacement."""
    new_password = "replacement-user-password"
    changed = await identity_client.put(
        "/v1/me/password",
        headers=identity_environment.authorization("user"),
        json={"password": new_password},
    )
    assert changed.status_code == 204

    old_login = await identity_client.post(
        "/v1/auth/login",
        json={
            "username": identity_environment.name("user"),
            "password": "regular-user-password",
        },
    )
    assert old_login.status_code == 401
    new_login = await identity_client.post(
        "/v1/auth/login",
        json={"username": identity_environment.name("user"), "password": new_password},
    )
    assert new_login.status_code == 200

    authenticated = await identity_client.get(
        "/v1/me",
        headers={"Authorization": f"Bearer {new_login.json()['access_token']}"},
    )
    assert authenticated.status_code == 200
    assert authenticated.json()["id"] == str(identity_environment.user_id)


@pytest.mark.anyio
@pytest.mark.integration
async def test_personal_token_lifecycle_enforces_ownership_and_revocation(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """A personal token is shown once, owner-revocable, and unusable after revocation."""
    user_headers = identity_environment.authorization("user")
    created = await identity_client.post(
        "/v1/me/api-tokens",
        headers=user_headers,
        json={"name": "personal-automation"},
    )
    assert created.status_code == 201
    token_payload = created.json()
    personal_token = token_payload["token"]

    listed = await identity_client.get("/v1/me/api-tokens", headers=user_headers)
    assert listed.status_code == 200
    listed_token = next(item for item in listed.json() if item["id"] == token_payload["id"])
    assert "token" not in listed_token
    assert listed_token["revoked_at"] is None

    authenticated = await identity_client.get(
        "/v1/me", headers={"Authorization": f"Bearer {personal_token}"}
    )
    assert authenticated.status_code == 200
    assert authenticated.json()["id"] == str(identity_environment.user_id)

    denied = await identity_client.delete(
        f"/v1/me/api-tokens/{token_payload['id']}",
        headers=identity_environment.authorization("admin"),
    )
    assert denied.status_code == 404
    still_authenticated = await identity_client.get(
        "/v1/me", headers={"Authorization": f"Bearer {personal_token}"}
    )
    assert still_authenticated.status_code == 200

    revoked = await identity_client.delete(
        f"/v1/me/api-tokens/{token_payload['id']}", headers=user_headers
    )
    assert revoked.status_code == 204
    rejected = await identity_client.get(
        "/v1/me", headers={"Authorization": f"Bearer {personal_token}"}
    )
    assert rejected.status_code == 401

    relisted = await identity_client.get("/v1/me/api-tokens", headers=user_headers)
    assert relisted.status_code == 200
    revoked_token = next(item for item in relisted.json() if item["id"] == token_payload["id"])
    assert revoked_token["revoked_at"] is not None


@pytest.mark.anyio
@pytest.mark.integration
async def test_service_token_is_shown_once_and_rejected_after_revocation(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """A service token authenticates, is hidden from listings, and stops after revocation."""
    admin_headers = identity_environment.authorization("admin")
    service_response = await identity_client.post(
        "/v1/service-identities",
        headers=admin_headers,
        json={
            "name": identity_environment.name("service"),
            "display_name": "Test service",
        },
    )
    assert service_response.status_code == 201
    service_id = service_response.json()["id"]

    token_response = await identity_client.post(
        f"/v1/service-identities/{service_id}/api-tokens",
        headers=admin_headers,
        json={"name": "automation"},
    )
    assert token_response.status_code == 201
    token_payload = token_response.json()
    service_token = token_payload["token"]

    listed_response = await identity_client.get(
        f"/v1/service-identities/{service_id}/api-tokens",
        headers=admin_headers,
    )
    assert listed_response.status_code == 200
    listed_token = next(
        item for item in listed_response.json() if item["id"] == token_payload["id"]
    )
    assert "token" not in listed_token

    me_response = await identity_client.get(
        "/v1/me",
        headers={"Authorization": f"Bearer {service_token}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["id"] == service_id

    revoke_response = await identity_client.delete(
        f"/v1/service-identities/{service_id}/api-tokens/{token_payload['id']}",
        headers=admin_headers,
    )
    assert revoke_response.status_code == 204

    rejected_response = await identity_client.get(
        "/v1/me",
        headers={"Authorization": f"Bearer {service_token}"},
    )
    assert rejected_response.status_code == 401


@pytest.mark.anyio
@pytest.mark.integration
async def test_administrator_can_manage_a_service_identity_lifecycle(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Service discovery, update, and archive expose state and disable its token."""
    headers = identity_environment.authorization("admin")
    created = await identity_client.post(
        "/v1/service-identities",
        headers=headers,
        json={
            "name": identity_environment.name("managed-service"),
            "display_name": "Managed service",
        },
    )
    assert created.status_code == 201
    service_id = created.json()["id"]

    listed = await identity_client.get("/v1/service-identities", headers=headers)
    assert listed.status_code == 200
    assert service_id in {item["id"] for item in listed.json()}

    retrieved = await identity_client.get(f"/v1/service-identities/{service_id}", headers=headers)
    assert retrieved.status_code == 200
    assert retrieved.json()["display_name"] == "Managed service"

    updated = await identity_client.patch(
        f"/v1/service-identities/{service_id}",
        headers=headers,
        json={"display_name": "Updated managed service"},
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "Updated managed service"

    issued = await identity_client.post(
        f"/v1/service-identities/{service_id}/api-tokens",
        headers=headers,
        json={"name": "managed-service-token"},
    )
    assert issued.status_code == 201
    service_token = issued.json()["token"]
    authenticated = await identity_client.get(
        "/v1/me", headers={"Authorization": f"Bearer {service_token}"}
    )
    assert authenticated.status_code == 200

    archived = await identity_client.delete(f"/v1/service-identities/{service_id}", headers=headers)
    assert archived.status_code == 204
    archived_state = await identity_client.get(
        f"/v1/service-identities/{service_id}", headers=headers
    )
    assert archived_state.status_code == 200
    assert archived_state.json()["archived_at"] is not None

    rejected = await identity_client.get(
        "/v1/me", headers={"Authorization": f"Bearer {service_token}"}
    )
    assert rejected.status_code == 401


@pytest.mark.anyio
@pytest.mark.integration
async def test_service_identity_creation_enforces_identity_invariants(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Verify service creation returns public state and enforces two restrictions.

    1. Reusing an existing principal name returns 409 Conflict.
    2. Supplying a password for a service identity returns 400 Bad Request.
    """
    headers = identity_environment.authorization("admin")
    name = identity_environment.name("created-service")
    created = await identity_client.post(
        "/v1/service-identities",
        headers=headers,
        json={"name": name, "display_name": "Created service"},
    )
    assert created.status_code == 201
    assert created.json()["kind"] == "Service"
    assert created.json()["role"] == "User"
    assert created.json()["name"] == name
    assert created.json()["display_name"] == "Created service"
    assert created.json()["archived_at"] is None
    assert "password" not in created.json()

    duplicate = await identity_client.post(
        "/v1/service-identities",
        headers=headers,
        json={"name": name, "display_name": "Duplicate service"},
    )
    assert duplicate.status_code == 409
    password_service = await identity_client.post(
        "/v1/service-identities",
        headers=headers,
        json={
            "name": identity_environment.name("password-service"),
            "display_name": "Password service",
            "password": "service-password-is-invalid",
        },
    )
    assert password_service.status_code == 400


@pytest.mark.anyio
@pytest.mark.integration
async def test_administrator_can_manage_a_user_lifecycle(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """An administrator can create, update, and archive a managed user."""
    admin_headers = identity_environment.authorization("admin")
    create_response = await identity_client.post(
        "/v1/users",
        headers=admin_headers,
        json={
            "name": identity_environment.name("managed"),
            "display_name": "Managed user",
            "password": "managed-user-password",
        },
    )
    assert create_response.status_code == 201
    user_id = create_response.json()["id"]

    update_response = await identity_client.patch(
        f"/v1/users/{user_id}",
        headers=admin_headers,
        json={"display_name": "Updated user"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["display_name"] == "Updated user"

    archive_response = await identity_client.delete(
        f"/v1/users/{user_id}",
        headers=admin_headers,
    )

    assert archive_response.status_code == 204


@pytest.mark.anyio
@pytest.mark.integration
async def test_user_creation_returns_public_state_and_rejects_duplicate_names(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """User creation returns usable public state while preserving unique names."""
    headers = identity_environment.authorization("admin")
    name = identity_environment.name("created-user")
    password = "created-user-password"
    request = {
        "name": name,
        "display_name": "Created user",
        "role": "User",
        "password": password,
    }
    created = await identity_client.post("/v1/users", headers=headers, json=request)
    assert created.status_code == 201
    assert created.json()["kind"] == "User"
    assert created.json()["role"] == "User"
    assert created.json()["name"] == name
    assert created.json()["display_name"] == "Created user"
    assert created.json()["archived_at"] is None
    assert "password" not in created.json()

    login = await identity_client.post(
        "/v1/auth/login", json={"username": name, "password": password}
    )
    assert login.status_code == 200
    duplicate = await identity_client.post("/v1/users", headers=headers, json=request)
    assert duplicate.status_code == 409


@pytest.mark.anyio
@pytest.mark.integration
async def test_user_listing_is_ordered_and_includes_archived_state(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Administrator user listing is deterministic and represents archived users."""
    headers = identity_environment.authorization("admin")
    created_users = []
    for prefix in ("listed-z", "listed-a"):
        created = await identity_client.post(
            "/v1/users",
            headers=headers,
            json={
                "name": identity_environment.name(prefix),
                "display_name": f"Listed {prefix}",
                "password": f"{prefix}-user-password",
            },
        )
        assert created.status_code == 201
        created_users.append(created.json())
    archived = await identity_client.delete(f"/v1/users/{created_users[1]['id']}", headers=headers)
    assert archived.status_code == 204
    service = await identity_client.post(
        "/v1/service-identities",
        headers=headers,
        json={
            "name": identity_environment.name("listed-service"),
            "display_name": "Listed service",
        },
    )
    assert service.status_code == 201

    response = await identity_client.get("/v1/users", headers=headers)

    assert response.status_code == 200
    names = [item["name"] for item in response.json()]
    assert names == sorted(names)
    users_by_id = {item["id"]: item for item in response.json()}
    assert created_users[0]["id"] in users_by_id
    assert users_by_id[created_users[1]["id"]]["archived_at"] is not None
    assert service.json()["id"] not in users_by_id


@pytest.mark.anyio
@pytest.mark.integration
async def test_user_update_persists_display_name_and_role(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Updating a user persists both mutable fields and returns the resulting state."""
    headers = identity_environment.authorization("admin")
    created = await identity_client.post(
        "/v1/users",
        headers=headers,
        json={
            "name": identity_environment.name("updated-user"),
            "display_name": "Before update",
            "password": "updated-user-password",
        },
    )
    assert created.status_code == 201
    user_id = created.json()["id"]

    updated = await identity_client.patch(
        f"/v1/users/{user_id}",
        headers=headers,
        json={"display_name": "After update", "role": "Admin"},
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "After update"
    assert updated.json()["role"] == "Admin"

    retrieved = await identity_client.get(f"/v1/users/{user_id}", headers=headers)
    assert retrieved.status_code == 200
    assert retrieved.json()["display_name"] == "After update"
    assert retrieved.json()["role"] == "Admin"


@pytest.mark.anyio
@pytest.mark.integration
async def test_user_archive_exposes_state_and_disables_existing_credentials(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Archiving a user remains observable while disabling its password and bearer tokens."""
    headers = identity_environment.authorization("admin")
    username = identity_environment.name("archived-user")
    password = "archived-user-password"
    created = await identity_client.post(
        "/v1/users",
        headers=headers,
        json={"name": username, "display_name": "Archived user", "password": password},
    )
    assert created.status_code == 201
    user_id = created.json()["id"]
    login = await identity_client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )
    assert login.status_code == 200
    access_token = login.json()["access_token"]

    archived = await identity_client.delete(f"/v1/users/{user_id}", headers=headers)
    assert archived.status_code == 204
    retrieved = await identity_client.get(f"/v1/users/{user_id}", headers=headers)
    assert retrieved.status_code == 200
    assert retrieved.json()["archived_at"] is not None
    listed = await identity_client.get("/v1/users", headers=headers)
    assert listed.status_code == 200
    archived_listing = next(item for item in listed.json() if item["id"] == user_id)
    assert archived_listing["archived_at"] is not None

    rejected_login = await identity_client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )
    assert rejected_login.status_code == 401
    rejected_token = await identity_client.get(
        "/v1/me", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert rejected_token.status_code == 401


@pytest.mark.anyio
@pytest.mark.integration
async def test_administrator_can_retrieve_and_reset_a_users_password(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """User retrieval returns the target and password reset replaces its login secret."""
    headers = identity_environment.authorization("admin")
    username = identity_environment.name("password-reset-user")
    old_password = "original-managed-password"
    new_password = "replacement-managed-password"
    created = await identity_client.post(
        "/v1/users",
        headers=headers,
        json={
            "name": username,
            "display_name": "Password reset user",
            "password": old_password,
        },
    )
    assert created.status_code == 201
    user_id = created.json()["id"]

    retrieved = await identity_client.get(f"/v1/users/{user_id}", headers=headers)
    assert retrieved.status_code == 200
    assert retrieved.json()["id"] == user_id
    assert retrieved.json()["name"] == username

    reset = await identity_client.put(
        f"/v1/users/{user_id}/password",
        headers=headers,
        json={"password": new_password},
    )
    assert reset.status_code == 204

    old_login = await identity_client.post(
        "/v1/auth/login", json={"username": username, "password": old_password}
    )
    assert old_login.status_code == 401
    new_login = await identity_client.post(
        "/v1/auth/login", json={"username": username, "password": new_password}
    )
    assert new_login.status_code == 200


@pytest.mark.anyio
@pytest.mark.integration
async def test_administrator_can_manage_a_group_membership_lifecycle(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """An administrator can create, update, archive, and change membership of a group."""
    admin_headers = identity_environment.authorization("admin")
    group_response = await identity_client.post(
        "/v1/groups",
        headers=admin_headers,
        json={"name": identity_environment.name("group"), "description": "Initial"},
    )
    assert group_response.status_code == 201
    group_id = group_response.json()["id"]

    membership_response = await identity_client.put(
        f"/v1/groups/{group_id}/members/{identity_environment.user_id}",
        headers=admin_headers,
    )
    assert membership_response.status_code == 204

    update_response = await identity_client.patch(
        f"/v1/groups/{group_id}",
        headers=admin_headers,
        json={"description": "Updated"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["description"] == "Updated"

    removal_response = await identity_client.delete(
        f"/v1/groups/{group_id}/members/{identity_environment.user_id}",
        headers=admin_headers,
    )
    assert removal_response.status_code == 204

    archive_response = await identity_client.delete(
        f"/v1/groups/{group_id}",
        headers=admin_headers,
    )

    assert archive_response.status_code == 204


@pytest.mark.anyio
@pytest.mark.integration
async def test_group_creation_returns_public_state_and_rejects_duplicate_names(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Group creation returns its stored state and preserves unique names."""
    headers = identity_environment.authorization("admin")
    request = {
        "name": identity_environment.name("created-group"),
        "description": "Created group",
    }
    created = await identity_client.post("/v1/groups", headers=headers, json=request)
    assert created.status_code == 201
    assert created.json()["name"] == request["name"]
    assert created.json()["description"] == "Created group"
    assert created.json()["archived_at"] is None

    duplicate = await identity_client.post("/v1/groups", headers=headers, json=request)
    assert duplicate.status_code == 409


@pytest.mark.anyio
@pytest.mark.integration
async def test_administrator_can_discover_a_groups_archival_state(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """Group list and retrieval expose both active and archived group state."""
    headers = identity_environment.authorization("admin")
    created = await identity_client.post(
        "/v1/groups",
        headers=headers,
        json={
            "name": identity_environment.name("discoverable-group"),
            "description": "Discoverable group",
        },
    )
    assert created.status_code == 201
    group_id = created.json()["id"]

    listed = await identity_client.get("/v1/groups", headers=headers)
    assert listed.status_code == 200
    listed_group = next(item for item in listed.json() if item["id"] == group_id)
    assert listed_group["archived_at"] is None

    retrieved = await identity_client.get(f"/v1/groups/{group_id}", headers=headers)
    assert retrieved.status_code == 200
    assert retrieved.json()["description"] == "Discoverable group"

    archived = await identity_client.delete(f"/v1/groups/{group_id}", headers=headers)
    assert archived.status_code == 204
    archived_state = await identity_client.get(f"/v1/groups/{group_id}", headers=headers)
    assert archived_state.status_code == 200
    assert archived_state.json()["archived_at"] is not None


@pytest.mark.anyio
@pytest.mark.integration
async def test_empty_group_patch_preserves_description(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """An empty group patch is a no-op rather than an implicit description clear."""
    headers = identity_environment.authorization("admin")
    create_response = await identity_client.post(
        "/v1/groups",
        headers=headers,
        json={"name": identity_environment.name("no-op-group"), "description": "Keep me"},
    )
    assert create_response.status_code == 201

    update_response = await identity_client.patch(
        f"/v1/groups/{create_response.json()['id']}",
        headers=headers,
        json={},
    )

    assert update_response.status_code == 200
    assert update_response.json()["description"] == "Keep me"


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize("action", ["archive", "demote"])
async def test_final_active_administrator_cannot_be_removed(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
    action: str,
) -> None:
    """Archiving or demoting the final usable administrator is rejected transactionally."""
    headers = identity_environment.authorization("admin")
    if action == "archive":
        response = await identity_client.delete(
            f"/v1/users/{identity_environment.admin_id}", headers=headers
        )
    else:
        response = await identity_client.patch(
            f"/v1/users/{identity_environment.admin_id}",
            headers=headers,
            json={"role": "User"},
        )

    assert response.status_code == 409
    authenticated = await identity_client.get("/v1/me", headers=headers)
    assert authenticated.status_code == 200


@pytest.mark.anyio
@pytest.mark.integration
async def test_identity_write_persists_its_audit_event(
    identity_environment: IdentityEnvironment,
    identity_client: httpx.AsyncClient,
) -> None:
    """A committed identity write records its actor, action, subject, and correlation ID."""
    correlation_id = f"identity-test-{identity_environment.suffix}"
    response = await identity_client.post(
        "/v1/service-identities",
        headers={
            **identity_environment.authorization("admin"),
            "X-Correlation-ID": correlation_id,
        },
        json={
            "name": identity_environment.name("audited-service"),
            "display_name": "Audited service",
        },
    )
    assert response.status_code == 201
    service_id = response.json()["id"]

    async with transaction(identity_environment.settings) as connection:
        cursor = await connection.execute(
            """
            SELECT actor_id, action, subject_type, subject_id, correlation_id, metadata
            FROM audit_events
            WHERE subject_type = 'Principal' AND subject_id = %s
            """,
            (service_id,),
        )
        audit_event = await cursor.fetchone()

    assert audit_event == (
        identity_environment.admin_id,
        "principal.created",
        "Principal",
        service_id,
        correlation_id,
        {},
    )


@pytest.mark.anyio
@pytest.mark.integration
async def test_identity_write_and_audit_event_roll_back_together(
    identity_environment: IdentityEnvironment,
) -> None:
    """A failed identity transaction persists neither its domain row nor its audit event."""
    name = identity_environment.name("rolled-back-service")
    subject_id: uuid.UUID | None = None

    with pytest.raises(RuntimeError, match="force identity rollback"):
        async with identity_transaction(identity_environment.settings) as connection:
            principal = await create_principal(
                connection,
                PrincipalCreate(name=name, display_name="Rolled back service"),
                "Service",
                identity_environment.admin_id,
            )
            subject_id = principal.id
            raise RuntimeError("force identity rollback")

    assert subject_id is not None
    async with transaction(identity_environment.settings) as connection:
        cursor = await connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM principals WHERE id = %s),
                (SELECT COUNT(*) FROM audit_events
                 WHERE subject_type = 'Principal' AND subject_id = %s)
            """,
            (subject_id, str(subject_id)),
        )
        persisted_counts = await cursor.fetchone()

    assert persisted_counts == (0, 0)


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize(
    "credential_state",
    ["expired", "archived"],
    ids=["expired-token", "archived-principal"],
)
async def test_inactive_credentials_cannot_authenticate(
    identity_environment: IdentityEnvironment,
    credential_state: Literal["expired", "archived"],
) -> None:
    """Authentication rejects expired tokens and tokens for archived principals."""
    principal_id = uuid.uuid4()
    token = f"srm_{uuid.uuid4().hex}"

    async with transaction(identity_environment.settings) as connection:
        await connection.execute(
            """
            INSERT INTO principals (id, kind, role, name, display_name, archived_at)
            VALUES (%s, 'Service', 'User', %s, 'Inactive principal', %s)
            """,
            (
                principal_id,
                identity_environment.name(credential_state),
                datetime.now(UTC) if credential_state == "archived" else None,
            ),
        )
        if credential_state == "expired":
            await connection.execute(
                """
                INSERT INTO api_tokens
                    (id, principal_id, name, token_hash, created_at, expires_at)
                VALUES (%s, %s, 'expired', %s, %s, %s)
                """,
                (
                    uuid.uuid4(),
                    principal_id,
                    token_hash(token),
                    datetime.now(UTC) - timedelta(days=2),
                    datetime.now(UTC) - timedelta(days=1),
                ),
            )
    if credential_state == "archived":
        async with identity_transaction(identity_environment.settings) as connection:
            token = (await issue_token(connection, principal_id, "archived", None)).token

    with pytest.raises(AuthenticationError):
        await authenticate_token(identity_environment.settings, token)


@pytest.mark.anyio
@pytest.mark.integration
async def test_concurrent_bootstrap_creates_exactly_one_administrator(
    monkeypatch: pytest.MonkeyPatch,
    integration_database_url: str,
) -> None:
    """Concurrent recovery ignores a legacy unusable admin and creates one usable admin."""
    database_url = integration_database_url
    schema = f"bootstrap_test_{uuid.uuid4().hex}"
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')
    schema_url = database_url_for_schema(database_url, schema)
    monkeypatch.setenv("SHEPHERD_DATABASE_URL", schema_url)
    get_settings.cache_clear()

    try:
        command.upgrade(Config("alembic.ini"), "head")
        settings = Settings(database_url=schema_url)
        async with transaction(settings) as connection:
            await connection.execute(
                """
                INSERT INTO principals (id, kind, role, name, display_name)
                VALUES (%s, 'User', 'Admin', 'legacy-admin', 'Legacy administrator')
                """,
                (uuid.uuid4(),),
            )
        results = await asyncio.gather(
            bootstrap_administrator("admin-one", "Admin one", "secure-password-one", settings),
            bootstrap_administrator("admin-two", "Admin two", "secure-password-two", settings),
            return_exceptions=True,
        )

        assert sum(isinstance(result, uuid.UUID) for result in results) == 1
        assert sum(isinstance(result, RuntimeError) for result in results) == 1
    finally:
        get_settings.cache_clear()
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
