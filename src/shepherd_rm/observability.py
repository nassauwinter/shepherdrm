"""Collect and render bounded operational metrics for Shepherd RM."""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    disable_created_metrics,
    generate_latest,
)

from shepherd_rm import __version__
from shepherd_rm.config import Settings
from shepherd_rm.database import transaction

METRIC_PREFIX = "shepherd_rm"
cast(Callable[[], None], disable_created_metrics)()
REVISION_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")
HTTP_METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})
HTTP_REQUESTS = Counter(
    "http_requests",
    "Completed HTTP requests.",
    ("method", "route", "status_class"),
    namespace=METRIC_PREFIX,
    registry=None,
)
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
    namespace=METRIC_PREFIX,
    registry=None,
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
ALLOCATION_ATTEMPTS = Counter(
    "allocation_attempts",
    "Lease allocation attempts by bounded outcome and sharing mode.",
    ("outcome", "sharing_mode"),
    namespace=METRIC_PREFIX,
    registry=None,
)
ALLOCATION_DURATION = Histogram(
    "allocation_duration_seconds",
    "Lease allocation duration in seconds.",
    ("outcome", "sharing_mode"),
    namespace=METRIC_PREFIX,
    registry=None,
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)


@dataclass(frozen=True)
class SharingMetrics:
    """Summarize current leases and resource use for one sharing mode."""

    active_leases: int
    utilized_resources: int
    total_resources: int


@dataclass(frozen=True)
class MetricsSnapshot:
    """Hold the database-derived values rendered during one scrape."""

    sharing: dict[str, SharingMetrics]
    expiration_backlog: int
    expiration_lag_seconds: float


@dataclass(frozen=True)
class WorkerMetricsSnapshot:
    """Hold bounded cross-process expiration-worker observations."""

    available: bool = False
    revision: str = "unknown"
    success_total: int = 0
    failure_total: int = 0
    expired_leases_total: int = 0
    last_success_timestamp: float = 0.0
    last_failure_timestamp: float = 0.0


def build_revision() -> str:
    """Return a safe source revision supplied by the immutable image build."""
    revision = os.getenv("SHEPHERD_BUILD_REVISION", "unknown").lower()
    return revision if REVISION_PATTERN.fullmatch(revision) else "unknown"


def record_http_request(method: str, route: str, status_code: int, duration_seconds: float) -> None:
    """Record one request using only bounded method, route-template, and status labels."""
    method = method if method in HTTP_METHODS else "OTHER"
    status_class = f"{status_code // 100}xx"
    HTTP_REQUESTS.labels(method=method, route=route, status_class=status_class).inc()
    HTTP_REQUEST_DURATION.labels(method=method, route=route).observe(duration_seconds)


def record_allocation(outcome: str, sharing_mode: str, duration_seconds: float) -> None:
    """Record one allocation attempt without resource or principal identifiers."""
    ALLOCATION_ATTEMPTS.labels(outcome=outcome, sharing_mode=sharing_mode).inc()
    ALLOCATION_DURATION.labels(outcome=outcome, sharing_mode=sharing_mode).observe(duration_seconds)


def load_worker_metrics(path: Path | None) -> WorkerMetricsSnapshot:
    """Read one complete worker snapshot or report that no valid signal exists."""
    if path is None:
        return WorkerMetricsSnapshot()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return WorkerMetricsSnapshot()
        revision = payload.get("revision")
        totals = (
            payload.get("success_total"),
            payload.get("failure_total"),
            payload.get("expired_leases_total"),
        )
        timestamps = (
            payload.get("last_success_timestamp"),
            payload.get("last_failure_timestamp"),
        )
        if not isinstance(revision, str) or revision != build_revision():
            return WorkerMetricsSnapshot()
        if any(type(value) is not int or value < 0 for value in totals):
            return WorkerMetricsSnapshot()
        if any(not isinstance(value, (int, float)) or value < 0 for value in timestamps):
            return WorkerMetricsSnapshot()
        success_total, failure_total, expired_leases_total = cast(tuple[int, int, int], totals)
        last_success_timestamp, last_failure_timestamp = cast(
            tuple[int | float, int | float], timestamps
        )
        return WorkerMetricsSnapshot(
            available=True,
            revision=revision,
            success_total=success_total,
            failure_total=failure_total,
            expired_leases_total=expired_leases_total,
            last_success_timestamp=float(last_success_timestamp),
            last_failure_timestamp=float(last_failure_timestamp),
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return WorkerMetricsSnapshot()


def update_worker_metrics(
    path: Path | None,
    outcome: Literal["success", "failure"],
    expired_leases: int = 0,
) -> None:
    """Atomically publish one worker-loop outcome for another process to scrape."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        current = load_worker_metrics(path)
        now = time.time()
        payload = {
            "revision": build_revision(),
            "success_total": current.success_total + (outcome == "success"),
            "failure_total": current.failure_total + (outcome == "failure"),
            "expired_leases_total": current.expired_leases_total + expired_leases,
            "last_success_timestamp": (
                now if outcome == "success" else current.last_success_timestamp
            ),
            "last_failure_timestamp": (
                now if outcome == "failure" else current.last_failure_timestamp
            ),
        }
        try:
            temporary_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            temporary_path.chmod(0o600)
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)


async def collect_database_metrics(settings: Settings) -> MetricsSnapshot:
    """Read lease and resource gauges from one short PostgreSQL transaction."""
    async with transaction(settings) as connection:
        sharing_cursor = await connection.execute(
            """
            WITH per_resource AS (
                SELECT
                    resources.id,
                    resources.sharing_mode,
                    resources.archived_at IS NULL
                        AND resources.operational_status = 'Active' AS available,
                    count(leases.id) AS active_leases
                FROM resources
                LEFT JOIN leases
                  ON leases.resource_id = resources.id
                 AND leases.ended_at IS NULL
                 AND (leases.expires_at IS NULL OR leases.expires_at > CURRENT_TIMESTAMP)
                GROUP BY resources.id, resources.sharing_mode, available
            )
            SELECT
                sharing_mode,
                coalesce(sum(active_leases), 0),
                count(*) FILTER (WHERE available AND active_leases > 0),
                count(*) FILTER (WHERE available)
            FROM per_resource
            GROUP BY sharing_mode
            """
        )
        sharing_rows = await sharing_cursor.fetchall()
        expiration_cursor = await connection.execute(
            """
            SELECT
                count(*),
                coalesce(
                    extract(epoch FROM CURRENT_TIMESTAMP - min(expires_at)),
                    0
                )
            FROM leases
            WHERE ended_at IS NULL
              AND expires_at IS NOT NULL
              AND expires_at <= CURRENT_TIMESTAMP
            """
        )
        expiration_row = await expiration_cursor.fetchone()

    sharing: dict[str, SharingMetrics] = {}
    for raw_row in sharing_rows:
        row = cast(tuple[str, int, int, int], raw_row)
        sharing[row[0]] = SharingMetrics(
            active_leases=row[1],
            utilized_resources=row[2],
            total_resources=row[3],
        )
    if expiration_row is None:
        raise RuntimeError("lease metrics query returned no row")
    expiration_backlog, expiration_lag_seconds = cast(tuple[int, float], expiration_row)
    return MetricsSnapshot(
        sharing=sharing,
        expiration_backlog=expiration_backlog,
        expiration_lag_seconds=max(0.0, float(expiration_lag_seconds)),
    )


def render_metrics(snapshot: MetricsSnapshot, worker: WorkerMetricsSnapshot) -> bytes:
    """Render process counters and one database snapshot in Prometheus format."""
    registry = CollectorRegistry()
    for collector in (
        HTTP_REQUESTS,
        HTTP_REQUEST_DURATION,
        ALLOCATION_ATTEMPTS,
        ALLOCATION_DURATION,
    ):
        registry.register(collector)

    build = Gauge(
        "build_info",
        "Build version and source revision.",
        ("version", "revision"),
        namespace=METRIC_PREFIX,
        registry=registry,
    )
    build.labels(version=__version__, revision=build_revision()).set(1)
    active = Gauge(
        "active_leases",
        "Effectively active leases.",
        ("sharing_mode",),
        namespace=METRIC_PREFIX,
        registry=registry,
    )
    utilization = Gauge(
        "resource_utilization_ratio",
        "Share of active resources with at least one active lease.",
        ("sharing_mode",),
        namespace=METRIC_PREFIX,
        registry=registry,
    )
    for sharing_mode in ("Exclusive", "Shared"):
        values = snapshot.sharing.get(sharing_mode, SharingMetrics(0, 0, 0))
        active.labels(sharing_mode=sharing_mode).set(values.active_leases)
        ratio = values.utilized_resources / values.total_resources if values.total_resources else 0
        utilization.labels(sharing_mode=sharing_mode).set(ratio)

    expired = Counter(
        "expired_leases",
        "Leases ended by the expiration worker.",
        namespace=METRIC_PREFIX,
        registry=registry,
    )
    expired.inc(worker.expired_leases_total)
    backlog = Gauge(
        "expiration_backlog_leases",
        "Unended leases whose expiration time has passed.",
        namespace=METRIC_PREFIX,
        registry=registry,
    )
    backlog.set(snapshot.expiration_backlog)
    lag = Gauge(
        "expiration_lag_seconds",
        "Age of the oldest overdue unended lease.",
        namespace=METRIC_PREFIX,
        registry=registry,
    )
    lag.set(snapshot.expiration_lag_seconds)
    worker_available = Gauge(
        "worker_metrics_available",
        "Whether a current-revision expiration-worker signal is available.",
        namespace=METRIC_PREFIX,
        registry=registry,
    )
    worker_available.set(worker.available)
    worker_loops = Counter(
        "worker_loops",
        "Expiration-worker loops by outcome.",
        ("outcome",),
        namespace=METRIC_PREFIX,
        registry=registry,
    )
    worker_loops.labels(outcome="success").inc(worker.success_total)
    worker_loops.labels(outcome="failure").inc(worker.failure_total)
    worker_last_loop = Gauge(
        "worker_last_loop_timestamp_seconds",
        "Unix timestamp of the last expiration-worker loop by outcome.",
        ("outcome",),
        namespace=METRIC_PREFIX,
        registry=registry,
    )
    worker_last_loop.labels(outcome="success").set(worker.last_success_timestamp)
    worker_last_loop.labels(outcome="failure").set(worker.last_failure_timestamp)
    return generate_latest(registry)
