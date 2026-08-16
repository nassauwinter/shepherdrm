import os

import httpx
import pytest

from shepherd_rm.application import create_app
from shepherd_rm.config import Settings


@pytest.mark.anyio
@pytest.mark.integration
async def test_readiness_checks_postgresql() -> None:
    """Readiness succeeds when the configured PostgreSQL instance accepts a query."""
    database_url = os.getenv("SHEPHERD_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("SHEPHERD_TEST_DATABASE_URL is not configured")

    app = create_app(settings=Settings(database_url=database_url))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
