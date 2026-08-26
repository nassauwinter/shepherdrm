"""Provide transaction boundaries for lease operations and expiration work."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncConnection

from shepherd_rm.config import Settings
from shepherd_rm.identity.database import identity_engine


@asynccontextmanager
async def leasing_transaction(settings: Settings) -> AsyncIterator[AsyncConnection]:
    """Commit a leasing transaction on success and roll it back on failure."""
    async with identity_engine(settings.database_url).begin() as connection:
        yield connection
