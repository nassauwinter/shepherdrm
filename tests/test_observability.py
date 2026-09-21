"""Verify that operational metrics remain bounded and free of identifiers."""

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from shepherd_rm import application, request_limits
from shepherd_rm.application import create_app
from shepherd_rm.config import Settings
from shepherd_rm.observability import (
    MetricsSnapshot,
    SharingMetrics,
    WorkerMetricsSnapshot,
    build_revision,
    load_worker_metrics,
    record_allocation,
    record_http_request,
    render_metrics,
    update_worker_metrics,
)


def test_build_revision_accepts_only_commit_identifiers() -> None:
    """Startup and build metrics never expose arbitrary environment content."""
    with patch.dict("os.environ", {"SHEPHERD_BUILD_REVISION": "a" * 40}):
        assert build_revision() == "a" * 40
    with patch.dict("os.environ", {"SHEPHERD_BUILD_REVISION": "secret/revision"}):
        assert build_revision() == "unknown"


def test_metrics_use_only_bounded_labels() -> None:
    """Request and allocation metrics omit raw paths, principals, and resource IDs."""
    path_canary = "resource-identifier-canary"
    principal_canary = "principal-identifier-canary"
    record_http_request("ATTACKER-METHOD", "<unmatched>", 404, 0.01)
    record_allocation("unavailable", "Exclusive", 0.02)

    output = render_metrics(
        MetricsSnapshot(
            sharing={"Exclusive": SharingMetrics(2, 1, 4)},
            expiration_backlog=1,
            expiration_lag_seconds=2.5,
        ),
        WorkerMetricsSnapshot(
            available=True,
            success_total=4,
            failure_total=1,
            expired_leases_total=3,
            last_success_timestamp=10.0,
            last_failure_timestamp=5.0,
        ),
    ).decode()

    assert 'method="OTHER"' in output
    assert 'route="<unmatched>"' in output
    assert 'outcome="unavailable",sharing_mode="Exclusive"' in output
    assert 'sharing_mode="Exclusive"} 2.0' in output
    assert "shepherd_rm_resource_utilization_ratio" in output
    assert "shepherd_rm_expiration_backlog_leases 1.0" in output
    assert "shepherd_rm_expiration_lag_seconds 2.5" in output
    assert 'shepherd_rm_worker_loops_total{outcome="success"} 4.0' in output
    assert 'shepherd_rm_worker_loops_total{outcome="failure"} 1.0' in output
    assert "_created" not in output
    second_output = render_metrics(
        MetricsSnapshot(sharing={}, expiration_backlog=0, expiration_lag_seconds=0),
        WorkerMetricsSnapshot(),
    ).decode()
    assert "_created" not in second_output
    assert path_canary not in output
    assert principal_canary not in output


@pytest.mark.anyio
async def test_body_limit_rejections_are_counted_without_raw_paths() -> None:
    """Transport-level rejections use a fixed route marker in request metrics."""
    app = create_app(settings=Settings(max_request_body_bytes=4))
    with patch.object(request_limits, "record_http_request") as record:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/secret/path", content="oversized")

    assert response.status_code == 413
    method, route, status_code, duration = record.call_args.args
    assert (method, route, status_code) == ("POST", "<unrouted>", 413)
    assert duration >= 0


@pytest.mark.anyio
async def test_unhandled_failures_are_counted_with_the_route_template() -> None:
    """Unexpected endpoint failures emit a bounded 500 metric before propagation."""
    app = create_app()

    @app.get("/failure/{record_id}")
    async def fail(record_id: str) -> None:
        raise RuntimeError(record_id)

    with patch.object(application, "record_http_request") as record:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get("/failure/secret-record-id")

    assert response.status_code == 500
    method, route, status_code, duration = record.call_args.args
    assert (method, route, status_code) == ("GET", "/failure/{record_id}", 500)
    assert duration >= 0


def test_worker_metrics_are_published_atomically(tmp_path: Path) -> None:
    """Worker outcomes survive process boundaries without exposing domain data."""
    state_path = tmp_path / "worker-metrics.json"

    update_worker_metrics(state_path, "success", expired_leases=2)
    update_worker_metrics(state_path, "success")
    update_worker_metrics(state_path, "failure")
    state = load_worker_metrics(state_path)

    assert state.available is True
    assert state.success_total == 2
    assert state.failure_total == 1
    assert state.expired_leases_total == 2
    assert state.last_success_timestamp > 0
    assert state.last_failure_timestamp > 0
    assert state_path.stat().st_mode & 0o777 == 0o600


def test_worker_metrics_replace_non_utf8_state(tmp_path: Path) -> None:
    """Corrupt state is ignored and repaired without stopping worker telemetry."""
    state_path = tmp_path / "worker-metrics.json"
    state_path.write_bytes(b"\xff\xfe")

    assert load_worker_metrics(state_path).available is False
    update_worker_metrics(state_path, "success")

    repaired = load_worker_metrics(state_path)
    assert repaired.available is True
    assert repaired.success_total == 1
