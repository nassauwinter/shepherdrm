"""Verify public application behavior without requiring external services."""

from collections.abc import AsyncIterator

import httpx
import pytest

from shepherd_rm.application import create_app
from shepherd_rm.config import Settings


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


@pytest.mark.anyio
async def test_validation_problem_does_not_reflect_secret_input() -> None:
    """A rejected password is never included in the validation response."""
    password = "secret-" + ("x" * 1024)
    app = create_app(readiness_check=ready)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/auth/login",
            json={"username": "admin", "password": password},
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert password not in response.text
    assert response.json()["detail"] == "The request did not satisfy the API contract"


@pytest.mark.anyio
async def test_declared_oversized_request_returns_safe_problem() -> None:
    """A request with an oversized content length is rejected without reading its secret."""
    secret = "secret-value-that-must-not-be-reflected"
    app = create_app(
        settings=Settings(max_request_body_bytes=16),
        readiness_check=ready,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/auth/login",
            content=secret,
            headers={"x-correlation-id": "oversized-request"},
        )

    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["x-correlation-id"] == "oversized-request"
    assert secret not in response.text
    assert response.json() == {
        "type": "about:blank",
        "title": "Payload too large",
        "status": 413,
        "detail": "The request body exceeds the configured size limit",
        "correlation_id": "oversized-request",
    }


@pytest.mark.anyio
async def test_streamed_oversized_request_returns_safe_problem() -> None:
    """A streamed request is rejected when its accumulated bytes cross the limit."""

    secret = b"streamed-secret-value"

    async def body_chunks() -> AsyncIterator[bytes]:
        yield secret[:8]
        yield secret[8:]

    app = create_app(
        settings=Settings(max_request_body_bytes=8),
        readiness_check=ready,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/auth/login",
            content=body_chunks(),
            headers={"x-correlation-id": "streamed-request"},
        )

    assert response.status_code == 413
    assert response.headers["x-correlation-id"] == "streamed-request"
    assert secret.decode() not in response.text
