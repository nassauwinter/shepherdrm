import os
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config

from shepherd_rm.application import create_app
from shepherd_rm.config import Settings, get_settings


def database_url_for_schema(database_url: str, schema: str) -> str:
    """Add a PostgreSQL search path without discarding existing URL parameters."""
    parts = urlsplit(database_url)
    parameters = parse_qsl(parts.query, keep_blank_values=True)
    existing_options = next((value for key, value in parameters if key == "options"), "")
    parameters = [(key, value) for key, value in parameters if key != "options"]
    parameters.append(("options", f"{existing_options} -csearch_path={schema}".strip()))
    return urlunsplit(parts._replace(query=urlencode(parameters)))


@pytest.mark.anyio
@pytest.mark.integration
async def test_readiness_checks_postgresql() -> None:
    """Readiness succeeds when PostgreSQL has the migration head shipped by the service."""
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


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize("revision", [None, "7280c6a57e19"], ids=["absent", "outdated"])
async def test_readiness_rejects_incompatible_schema(
    monkeypatch: pytest.MonkeyPatch,
    revision: str | None,
) -> None:
    """Readiness fails when migrations are either absent or behind the packaged head."""
    database_url = os.getenv("SHEPHERD_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("SHEPHERD_TEST_DATABASE_URL is not configured")

    schema = f"readiness_test_{uuid.uuid4().hex}"
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')

    schema_url = database_url_for_schema(database_url, schema)
    try:
        if revision is not None:
            monkeypatch.setenv("SHEPHERD_DATABASE_URL", schema_url)
            get_settings.cache_clear()
            command.upgrade(Config("alembic.ini"), revision)

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
        get_settings.cache_clear()
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
