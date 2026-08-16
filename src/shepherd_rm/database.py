from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import psycopg
from alembic.script import ScriptDirectory
from psycopg import AsyncConnection

from shepherd_rm.config import Settings


def sqlalchemy_database_url(database_url: str) -> str:
    """Select psycopg 3 explicitly when SQLAlchemy receives a PostgreSQL URL."""
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


@asynccontextmanager
async def transaction(settings: Settings) -> AsyncIterator[AsyncConnection[tuple[object, ...]]]:
    """Provide a short transaction that commits or rolls back on context exit."""
    async with await psycopg.AsyncConnection.connect(
        settings.database_url,
        connect_timeout=settings.database_connect_timeout_seconds,
    ) as connection:
        yield connection


async def lock_resource(connection: AsyncConnection[tuple[object, ...]], resource_id: UUID) -> bool:
    """Lock a resource row for an allocation-affecting transaction."""
    cursor = await connection.execute(
        "SELECT id FROM resources WHERE id = %s FOR UPDATE",
        (resource_id,),
    )
    return await cursor.fetchone() is not None


def expected_migration_heads(settings: Settings) -> frozenset[str]:
    """Read the migration heads shipped with this service build."""
    return frozenset(ScriptDirectory(str(settings.migrations_path)).get_heads())


async def check_database(settings: Settings) -> None:
    async with transaction(settings) as connection:
        cursor = await connection.execute("SELECT version_num FROM alembic_version")
        applied_heads = frozenset(str(row[0]) for row in await cursor.fetchall())
        packaged_heads = expected_migration_heads(settings)
        if applied_heads != packaged_heads:
            raise RuntimeError(
                f"database migration heads {sorted(applied_heads)} do not match "
                f"service migration heads {sorted(packaged_heads)}"
            )
