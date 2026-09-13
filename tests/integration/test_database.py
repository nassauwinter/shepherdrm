"""Verify transaction, timestamp, and row-lock behavior against PostgreSQL."""

from __future__ import annotations

import asyncio
import uuid

import psycopg
import pytest

from shepherd_rm.config import Settings
from shepherd_rm.database import lock_resource, transaction
from tests.integration.support import (
    acquire_competing_resource_lock,
    wait_until_backend_waits_for_lock,
)


@pytest.mark.anyio
@pytest.mark.integration
async def test_transaction_rolls_back_on_failure(
    integration_database_url: str,
    integration_settings: Settings,
) -> None:
    """The transaction helper rolls back all writes when its body raises an error."""
    resource_id = uuid.uuid4()

    with pytest.raises(RuntimeError, match="force rollback"):
        async with transaction(integration_settings) as connection:
            await connection.execute(
                """
                INSERT INTO resources (id, name, type, sharing_mode)
                VALUES (%s, %s, %s, %s)
                """,
                (resource_id, f"rollback-{resource_id}", "test-resource", "Exclusive"),
            )
            raise RuntimeError("force rollback")

    async with await psycopg.AsyncConnection.connect(integration_database_url) as connection:
        cursor = await connection.execute(
            "SELECT COUNT(*) FROM resources WHERE id = %s",
            (resource_id,),
        )
        row = await cursor.fetchone()

    assert row == (0,)


@pytest.mark.anyio
@pytest.mark.integration
async def test_resource_lock_serializes_competing_transactions(
    integration_database_url: str,
    integration_settings: Settings,
) -> None:
    """A second resource lock waits until the transaction holding the row commits."""
    resource_id = uuid.uuid4()
    async with transaction(integration_settings) as connection:
        await connection.execute(
            """
            INSERT INTO resources (id, name, type, sharing_mode)
            VALUES (%s, %s, %s, %s)
            """,
            (resource_id, f"locking-{resource_id}", "test-resource", "Exclusive"),
        )

    competing_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    try:
        async with asyncio.TaskGroup() as tasks:
            async with transaction(integration_settings) as connection:
                assert await lock_resource(connection, resource_id)
                competing_task = tasks.create_task(
                    acquire_competing_resource_lock(
                        integration_settings,
                        resource_id,
                        competing_pid,
                    )
                )

                async with asyncio.timeout(5):
                    pid = await competing_pid
                    await wait_until_backend_waits_for_lock(integration_database_url, pid)

                assert not competing_task.done()

        assert competing_task.result() is True
    finally:
        async with transaction(integration_settings) as connection:
            await connection.execute("DELETE FROM resources WHERE id = %s", (resource_id,))


@pytest.mark.anyio
@pytest.mark.integration
async def test_raw_updates_advance_updated_at(integration_settings: Settings) -> None:
    """PostgreSQL advances updated_at even when a resource is changed through raw SQL."""
    resource_id = uuid.uuid4()

    try:
        async with transaction(integration_settings) as connection:
            cursor = await connection.execute(
                """
                INSERT INTO resources (id, name, type, sharing_mode)
                VALUES (%s, %s, %s, %s)
                RETURNING updated_at
                """,
                (resource_id, f"timestamp-{resource_id}", "test-resource", "Exclusive"),
            )
            inserted_row = await cursor.fetchone()
            assert inserted_row is not None
            inserted_at = inserted_row[0]

        await asyncio.sleep(0.01)
        async with transaction(integration_settings) as connection:
            cursor = await connection.execute(
                "UPDATE resources SET name = %s WHERE id = %s RETURNING updated_at",
                (f"updated-{resource_id}", resource_id),
            )
            updated_row = await cursor.fetchone()
            assert updated_row is not None
            updated_at = updated_row[0]

        assert updated_at > inserted_at
    finally:
        async with transaction(integration_settings) as connection:
            await connection.execute("DELETE FROM resources WHERE id = %s", (resource_id,))


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("expiration_mode", "default_ttl", "max_ttl"),
    [
        ("Required", None, None),
        ("Optional", 0, None),
        ("Optional", None, 0),
        ("Optional", 7200, 3600),
    ],
    ids=[
        "required-without-default",
        "non-positive-default",
        "non-positive-maximum",
        "default-above-maximum",
    ],
)
async def test_database_enforces_required_positive_and_ordered_expiration_limits(
    integration_settings: Settings,
    expiration_mode: str,
    default_ttl: int | None,
    max_ttl: int | None,
) -> None:
    """The database enforces three expiration-limit conditions.

    - Required mode has a default TTL.
    - Default and maximum TTL values are positive when present.
    - The default TTL does not exceed the maximum TTL.
    """
    resource_id = uuid.uuid4()

    with pytest.raises(psycopg.errors.CheckViolation):
        async with transaction(integration_settings) as connection:
            await connection.execute(
                """
                INSERT INTO resources
                    (id, name, type, sharing_mode, expiration_mode,
                     default_ttl_seconds, max_ttl_seconds)
                VALUES (%s, %s, 'environment', 'Exclusive', %s, %s, %s)
                """,
                (
                    resource_id,
                    f"invalid-policy-{resource_id}",
                    expiration_mode,
                    default_ttl,
                    max_ttl,
                ),
            )
