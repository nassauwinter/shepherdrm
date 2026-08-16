"""Verify Alembic upgrades, downgrades, and model consistency on PostgreSQL."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from shepherd_rm.config import get_settings
from shepherd_rm.database import sqlalchemy_database_url
from tests.integration.support import database_url_for_schema

EXPECTED_TABLES = {
    "alembic_version",
    "api_tokens",
    "audit_events",
    "group_memberships",
    "groups",
    "leases",
    "password_credentials",
    "principals",
    "resources",
}


@pytest.mark.integration
def test_initial_migration_upgrades_and_downgrades_postgresql(
    monkeypatch: pytest.MonkeyPatch,
    integration_database_url: str,
) -> None:
    """The initial migration creates the domain schema and can remove it cleanly."""
    database_url = integration_database_url

    schema = f"migration_test_{uuid.uuid4().hex}"
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')

    schema_url = database_url_for_schema(database_url, schema)
    monkeypatch.setenv("SHEPHERD_DATABASE_URL", schema_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    engine = create_engine(sqlalchemy_database_url(schema_url))

    try:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) == EXPECTED_TABLES

        command.check(config)
        command.downgrade(config, "base")
        with engine.connect() as connection:
            remaining_tables = set(inspect(connection).get_table_names())
        assert remaining_tables <= {"alembic_version"}
    finally:
        engine.dispose()
        get_settings.cache_clear()
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
