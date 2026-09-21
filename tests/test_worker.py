"""Verify expiration-worker observability around successful and failed loops."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from shepherd_rm.config import Settings
from shepherd_rm.leasing import worker
from shepherd_rm.observability import load_worker_metrics


@asynccontextmanager
async def successful_transaction(_settings: Settings) -> AsyncIterator[object]:
    """Yield a stand-in connection for one successful worker loop."""
    yield object()


@asynccontextmanager
async def failed_transaction(_settings: Settings) -> AsyncIterator[object]:
    """Raise the database failure exercised by the worker regression."""
    raise RuntimeError("database unavailable")
    yield object()


@pytest.mark.anyio
async def test_empty_successful_loop_publishes_worker_signal(tmp_path: Path) -> None:
    """A loop with no expirations still advances the cross-process heartbeat."""
    state_path = tmp_path / "worker-metrics.json"
    settings = Settings(worker_metrics_path=state_path)
    with (
        patch.object(worker, "get_settings", return_value=settings),
        patch.object(worker, "leasing_transaction", successful_transaction),
        patch.object(worker, "expire_due_leases", AsyncMock(return_value=0)),
        patch.object(worker.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)),
        pytest.raises(asyncio.CancelledError),
    ):
        await worker.run_worker()

    state = load_worker_metrics(state_path)
    assert state.success_total == 1
    assert state.failure_total == 0
    assert state.last_success_timestamp > 0


@pytest.mark.anyio
async def test_failed_loop_publishes_failure_before_exit(tmp_path: Path) -> None:
    """A database failure advances the failure signal before the worker exits."""
    state_path = tmp_path / "worker-metrics.json"
    settings = Settings(worker_metrics_path=state_path)
    with (
        patch.object(worker, "get_settings", return_value=settings),
        patch.object(worker, "leasing_transaction", failed_transaction),
        pytest.raises(RuntimeError, match="database unavailable"),
    ):
        await worker.run_worker()

    state = load_worker_metrics(state_path)
    assert state.success_total == 0
    assert state.failure_total == 1
    assert state.last_failure_timestamp > 0


@pytest.mark.anyio
async def test_metrics_write_failure_does_not_stop_expiration(tmp_path: Path) -> None:
    """An unavailable telemetry file cannot terminate a healthy worker loop."""
    settings = Settings(worker_metrics_path=tmp_path / "unwritable" / "state.json")
    expire = AsyncMock(return_value=0)
    with (
        patch.object(worker, "get_settings", return_value=settings),
        patch.object(worker, "leasing_transaction", successful_transaction),
        patch.object(worker, "expire_due_leases", expire),
        patch.object(worker, "update_worker_metrics", side_effect=OSError("read-only")),
        patch.object(worker.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)),
        pytest.raises(asyncio.CancelledError),
    ):
        await worker.run_worker()

    expire.assert_awaited_once()
