"""Provide the SQLAlchemy transaction boundary used by the identity feature."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from shepherd_rm.config import Settings
from shepherd_rm.database import sqlalchemy_database_url


@lru_cache
def identity_engine(database_url: str) -> AsyncEngine:
    """Create a reusable async engine without retaining pooled test connections."""
    return create_async_engine(sqlalchemy_database_url(database_url), poolclass=NullPool)


@asynccontextmanager
async def identity_transaction(settings: Settings) -> AsyncIterator[AsyncConnection]:
    """Commit a feature transaction on success and roll it back on failure."""
    async with identity_engine(settings.database_url).begin() as connection:
        yield connection
