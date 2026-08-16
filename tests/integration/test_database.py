from __future__ import annotations

import asyncio
import os
import uuid

import psycopg
import pytest

from shepherd_rm.config import Settings
from shepherd_rm.database import lock_resource, transaction


def database_url_or_skip() -> str:
    database_url = os.getenv("SHEPHERD_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("SHEPHERD_TEST_DATABASE_URL is not configured")
    return database_url


@pytest.mark.anyio
@pytest.mark.integration
async def test_transaction_rolls_back_on_failure() -> None:
    """The transaction helper rolls back all writes when its body raises an error."""
    database_url = database_url_or_skip()
    resource_id = uuid.uuid4()
    settings = Settings(database_url=database_url)

    with pytest.raises(RuntimeError, match="force rollback"):
        async with transaction(settings) as connection:
            await connection.execute(
                """
                INSERT INTO resources (id, name, type, sharing_mode)
                VALUES (%s, %s, %s, %s)
                """,
                (resource_id, f"rollback-{resource_id}", "test-resource", "Exclusive"),
            )
            raise RuntimeError("force rollback")

    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        cursor = await connection.execute(
            "SELECT COUNT(*) FROM resources WHERE id = %s",
            (resource_id,),
        )
        row = await cursor.fetchone()

    assert row == (0,)


@pytest.mark.anyio
@pytest.mark.integration
async def test_resource_lock_serializes_competing_transactions() -> None:
    """A second resource lock waits until the transaction holding the row commits."""
    database_url = database_url_or_skip()
    resource_id = uuid.uuid4()
    settings = Settings(database_url=database_url)
    async with transaction(settings) as connection:
        await connection.execute(
            """
            INSERT INTO resources (id, name, type, sharing_mode)
            VALUES (%s, %s, %s, %s)
            """,
            (resource_id, f"locking-{resource_id}", "test-resource", "Exclusive"),
        )

    competing_started = asyncio.Event()

    async def acquire_competing_lock() -> bool:
        async with transaction(settings) as connection:
            competing_started.set()
            return await lock_resource(connection, resource_id)

    try:
        async with transaction(settings) as connection:
            assert await lock_resource(connection, resource_id)
            competing_task = asyncio.create_task(acquire_competing_lock())
            await competing_started.wait()
            await asyncio.sleep(0.1)
            assert not competing_task.done()

        assert await asyncio.wait_for(competing_task, timeout=1) is True
    finally:
        async with transaction(settings) as connection:
            await connection.execute("DELETE FROM resources WHERE id = %s", (resource_id,))


@pytest.mark.anyio
@pytest.mark.integration
async def test_raw_updates_advance_updated_at() -> None:
    """PostgreSQL advances updated_at even when a resource is changed through raw SQL."""
    database_url = database_url_or_skip()
    resource_id = uuid.uuid4()
    settings = Settings(database_url=database_url)

    try:
        async with transaction(settings) as connection:
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
        async with transaction(settings) as connection:
            cursor = await connection.execute(
                "UPDATE resources SET name = %s WHERE id = %s RETURNING updated_at",
                (f"updated-{resource_id}", resource_id),
            )
            updated_row = await cursor.fetchone()
            assert updated_row is not None
            updated_at = updated_row[0]

        assert updated_at > inserted_at
    finally:
        async with transaction(settings) as connection:
            await connection.execute("DELETE FROM resources WHERE id = %s", (resource_id,))
