"""Run the database-backed lease expiration loop as a standalone process."""

from __future__ import annotations

import asyncio
import logging

from shepherd_rm.config import get_settings
from shepherd_rm.leasing.database import leasing_transaction
from shepherd_rm.leasing.persistence import expire_due_leases
from shepherd_rm.logging import configure_logging

LOGGER = logging.getLogger(__name__)


async def run_worker() -> None:
    """Continuously expire bounded batches without holding idle transactions."""
    settings = get_settings()
    configure_logging(settings.log_level)
    while True:
        async with leasing_transaction(settings) as connection:
            expired = await expire_due_leases(connection, settings.lease_expiration_batch_size)
        if expired:
            LOGGER.info("expired leases", extra={"expired_count": expired})
            continue
        await asyncio.sleep(settings.lease_expiration_poll_seconds)


def run() -> None:
    """Start the lease expiration worker from its console entry point."""
    asyncio.run(run_worker())
