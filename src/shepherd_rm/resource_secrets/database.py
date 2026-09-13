"""Provide the transaction boundary used by resource-secret operations."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncConnection

from shepherd_rm.config import Settings
from shepherd_rm.identity.database import identity_engine


@asynccontextmanager
async def resource_secret_transaction(settings: Settings) -> AsyncIterator[AsyncConnection]:
    """Commit a resource-secret transaction on success and roll it back on failure."""
    async with identity_engine(settings.database_url).begin() as connection:
        yield connection
