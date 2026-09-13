"""Verify readiness behavior for current, missing, and outdated schemas."""

import uuid

import httpx
import psycopg
import pytest

from shepherd_rm.application import create_app
from shepherd_rm.config import Settings
from tests.integration.support import database_url_for_schema


@pytest.mark.anyio
@pytest.mark.integration
async def test_readiness_checks_postgresql(integration_database_url: str) -> None:
    """Readiness succeeds when PostgreSQL has the migration head shipped by the service."""
    app = create_app(settings=Settings(database_url=integration_database_url))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize("revision", [None, "000000000000"], ids=["absent", "outdated"])
async def test_readiness_rejects_incompatible_schema(
    integration_database_url: str,
    revision: str | None,
) -> None:
    """Readiness fails when migrations are either absent or behind the packaged head."""
    schema = f"readiness_test_{uuid.uuid4().hex}"
    with psycopg.connect(integration_database_url, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')

    schema_url = database_url_for_schema(integration_database_url, schema)
    try:
        if revision is not None:
            with psycopg.connect(schema_url, autocommit=True) as connection:
                connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
                connection.execute("INSERT INTO alembic_version VALUES (%s)", (revision,))

        app = create_app(settings=Settings(database_url=schema_url))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/ready")

        assert response.status_code == 503
        assert response.json()["detail"] == (
            "PostgreSQL is unavailable or its schema is incompatible"
        )
    finally:
        with psycopg.connect(integration_database_url, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
