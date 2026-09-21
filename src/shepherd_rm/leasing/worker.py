"""Run the database-backed lease expiration loop as a standalone process."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Literal

from shepherd_rm import __version__
from shepherd_rm.config import get_settings
from shepherd_rm.leasing.database import leasing_transaction
from shepherd_rm.leasing.persistence import expire_due_leases
from shepherd_rm.logging import configure_logging
from shepherd_rm.observability import build_revision, update_worker_metrics

LOGGER = logging.getLogger(__name__)


def publish_worker_metrics(
    settings_path: Path | None,
    outcome: Literal["success", "failure"],
    expired: int = 0,
) -> None:
    """Publish telemetry without allowing its failure to stop expiration work."""
    try:
        update_worker_metrics(settings_path, outcome, expired)
    except OSError:
        LOGGER.exception("worker metrics update failed")


async def run_worker() -> None:
    """Continuously expire bounded batches without holding idle transactions."""
    settings = get_settings()
    configure_logging(settings.log_level)
    LOGGER.info(
        "expiration worker starting",
        extra={"version": __version__, "revision": build_revision()},
    )
    while True:
        started_at = time.perf_counter()
        try:
            async with leasing_transaction(settings) as connection:
                expired = await expire_due_leases(connection, settings.lease_expiration_batch_size)
        except Exception:
            publish_worker_metrics(settings.worker_metrics_path, "failure")
            LOGGER.exception(
                "expiration worker loop failed",
                extra={"duration_ms": round((time.perf_counter() - started_at) * 1000, 3)},
            )
            raise
        publish_worker_metrics(settings.worker_metrics_path, "success", expired)
        if expired:
            LOGGER.info(
                "expired leases",
                extra={
                    "expired_count": expired,
                    "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
                },
            )
            continue
        await asyncio.sleep(settings.lease_expiration_poll_seconds)


def run() -> None:
    """Start the lease expiration worker from its console entry point."""
    asyncio.run(run_worker())
