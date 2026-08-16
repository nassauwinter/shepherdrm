import httpx
import pytest

from shepherd_rm.application import create_app


async def ready() -> None:
    return None


async def not_ready() -> None:
    raise RuntimeError("database unavailable")


@pytest.mark.anyio
async def test_health_reports_liveness_and_correlation_id() -> None:
    """The health endpoint reports liveness and preserves a safe correlation ID."""
    app = create_app(readiness_check=ready)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health", headers={"x-correlation-id": "test-request"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-correlation-id"] == "test-request"


@pytest.mark.anyio
async def test_readiness_reports_available_database() -> None:
    """The readiness endpoint succeeds when its database check succeeds."""
    app = create_app(readiness_check=ready)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.anyio
async def test_readiness_returns_problem_when_database_is_unavailable() -> None:
    """The readiness endpoint returns a safe problem response for database failure."""
    app = create_app(readiness_check=not_ready)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/ready",
            headers={"x-correlation-id": "failed-request"},
        )

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "about:blank",
        "title": "Service unavailable",
        "status": 503,
        "detail": "PostgreSQL is unavailable or its schema is incompatible",
        "correlation_id": "failed-request",
    }


@pytest.mark.anyio
async def test_server_exposes_committed_openapi_contract() -> None:
    """The runtime OpenAPI endpoint serves the committed authoritative contract."""
    app = create_app(readiness_check=ready)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json() == app.openapi()
