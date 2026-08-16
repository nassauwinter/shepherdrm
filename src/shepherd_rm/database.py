import psycopg

from shepherd_rm.config import Settings


async def check_database(settings: Settings) -> None:
    async with await psycopg.AsyncConnection.connect(
        settings.database_url,
        connect_timeout=settings.database_connect_timeout_seconds,
    ) as connection:
        await connection.execute("SELECT 1")
