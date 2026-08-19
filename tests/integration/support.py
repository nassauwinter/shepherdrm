"""Provide reusable data and coordination helpers for integration tests."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import psycopg
from sqlalchemy import text

from shepherd_rm.catalog.database import catalog_transaction
from shepherd_rm.catalog.models import ResourceResponse
from shepherd_rm.catalog.persistence import set_resource_grant, transition_resource
from shepherd_rm.config import Settings
from shepherd_rm.database import lock_resource, transaction


@dataclass(frozen=True)
class IdentityEnvironment:
    """Hold uniquely named principals and credentials owned by one test."""

    settings: Settings
    suffix: str
    admin_id: uuid.UUID
    user_id: uuid.UUID
    admin_token: str
    user_token: str

    def name(self, prefix: str) -> str:
        """Return a unique record name that the fixture cleanup owns."""
        return f"{prefix}-{self.suffix}"

    def authorization(self, actor: Literal["admin", "user"]) -> dict[str, str]:
        """Build an authorization header for one of the fixture principals."""
        token = self.admin_token if actor == "admin" else self.user_token
        return {"Authorization": f"Bearer {token}"}


@dataclass(frozen=True)
class AuthenticatedTestUser:
    """Identify an API-created user and provide its bearer authorization header."""

    id: uuid.UUID
    headers: dict[str, str]


async def create_test_resource(
    client: httpx.AsyncClient,
    environment: IdentityEnvironment,
    prefix: str,
    *,
    resource_type: str = "environment",
    sharing_mode: Literal["Exclusive", "Shared"] = "Exclusive",
    visibility_mode: Literal["Public", "Restricted"] = "Restricted",
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create one uniquely named resource and assert the setup request succeeded."""
    response = await client.post(
        "/v1/resources",
        headers=environment.authorization("admin"),
        json={
            "name": environment.name(prefix),
            "type": resource_type,
            "sharing_mode": sharing_mode,
            "visibility_mode": visibility_mode,
            "labels": labels or {},
        },
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


async def create_authenticated_test_user(
    client: httpx.AsyncClient,
    environment: IdentityEnvironment,
    prefix: str,
) -> AuthenticatedTestUser:
    """Create and authenticate one uniquely named regular user for test setup."""
    name = environment.name(prefix)
    password = "catalog-test-user-password"
    created = await client.post(
        "/v1/users",
        headers=environment.authorization("admin"),
        json={"name": name, "display_name": "Catalog test user", "password": password},
    )
    assert created.status_code == 201
    principal_id = uuid.UUID(created.json()["id"])
    login = await client.post(
        "/v1/auth/login",
        json={"username": name, "password": password},
    )
    assert login.status_code == 200
    return AuthenticatedTestUser(
        id=principal_id,
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )


async def create_test_group(
    client: httpx.AsyncClient,
    environment: IdentityEnvironment,
    prefix: str,
) -> uuid.UUID:
    """Create one uniquely named group and assert the setup request succeeded."""
    response = await client.post(
        "/v1/groups",
        headers=environment.authorization("admin"),
        json={"name": environment.name(prefix)},
    )
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


def database_url_for_schema(database_url: str, schema: str) -> str:
    """Add a PostgreSQL search path without discarding existing URL parameters."""
    parts = urlsplit(database_url)
    parameters = parse_qsl(parts.query, keep_blank_values=True)
    existing_options = next((value for key, value in parameters if key == "options"), "")
    parameters = [(key, value) for key, value in parameters if key != "options"]
    options = f"{existing_options} -csearch_path={schema}".strip()
    parameters.append(("options", options))
    return urlunsplit(parts._replace(query=urlencode(parameters)))


async def acquire_competing_resource_lock(
    settings: Settings,
    resource_id: uuid.UUID,
    backend_pid: asyncio.Future[int],
) -> bool:
    """Publish a connection's backend PID before attempting a resource lock."""
    async with transaction(settings) as connection:
        cursor = await connection.execute("SELECT pg_backend_pid()")
        row = await cursor.fetchone()
        assert row is not None
        backend_pid.set_result(cast(int, row[0]))
        return await lock_resource(connection, resource_id)


async def wait_until_backend_waits_for_lock(database_url: str, backend_pid: int) -> None:
    """Wait until PostgreSQL reports that a backend is blocked on a lock."""
    async with await psycopg.AsyncConnection.connect(database_url) as observer:
        while True:
            cursor = await observer.execute(
                "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                (backend_pid,),
            )
            if await cursor.fetchone() == ("Lock",):
                return
            await asyncio.sleep(0.01)


async def transition_resource_with_backend_pid(
    settings: Settings,
    resource_id: uuid.UUID,
    actor_id: uuid.UUID,
    backend_pid: asyncio.Future[int],
) -> ResourceResponse:
    """Publish a catalog transaction's PID before attempting a resource transition."""
    async with catalog_transaction(settings) as connection:
        pid = await connection.scalar(text("SELECT pg_backend_pid()"))
        assert pid is not None
        backend_pid.set_result(int(pid))
        return await transition_resource(connection, resource_id, "quarantine", actor_id)


async def grant_resource_with_backend_pid(
    settings: Settings,
    resource_id: uuid.UUID,
    target_id: uuid.UUID,
    actor_id: uuid.UUID,
    backend_pid: asyncio.Future[int],
) -> None:
    """Publish a grant transaction's PID before it attempts the resource row lock."""
    async with catalog_transaction(settings) as connection:
        pid = await connection.scalar(text("SELECT pg_backend_pid()"))
        assert pid is not None
        backend_pid.set_result(int(pid))
        await set_resource_grant(connection, resource_id, target_id, "Principal", True, actor_id)
