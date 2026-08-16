"""Provide reusable data and coordination helpers for integration tests."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg

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
        backend_pid.set_result(int(row[0]))
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
