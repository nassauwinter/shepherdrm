"""Verify Alembic upgrades, downgrades, and model consistency on PostgreSQL."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

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
    "rate_limit_windows",
    "resource_group_grants",
    "resource_principal_grants",
    "resource_secrets",
    "resources",
}


@pytest.mark.integration
def test_initial_migration_upgrades_and_downgrades_postgresql(
    monkeypatch: pytest.MonkeyPatch,
    integration_database_url: str,
) -> None:
    """The baseline creates the final schema and defaults and downgrades cleanly."""
    database_url = integration_database_url

    schema = f"migration_test_{uuid.uuid4().hex}"
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')

    schema_url = database_url_for_schema(database_url, schema)
    monkeypatch.setenv("SHEPHERD_DATABASE_URL", schema_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    engine = create_engine(sqlalchemy_database_url(schema_url))
    resource_id = uuid.uuid4()

    try:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            inspector = inspect(connection)
            assert set(inspector.get_table_names()) == EXPECTED_TABLES
            connection.execute(
                text(
                    """
                    INSERT INTO resources (id, name, type, sharing_mode)
                    VALUES (:id, 'baseline-resource', 'environment', 'Exclusive')
                    """
                ),
                {"id": resource_id},
            )
            resource_defaults = connection.execute(
                text(
                    """
                    SELECT visibility_mode, expiration_mode,
                           default_ttl_seconds, max_ttl_seconds
                    FROM resources
                    WHERE id = :id
                    """
                ),
                {"id": resource_id},
            ).one()
            lease_columns = {column["name"]: column for column in inspector.get_columns("leases")}
            resource_indexes = {index["name"] for index in inspector.get_indexes("resources")}
            membership_indexes = {
                index["name"] for index in inspector.get_indexes("group_memberships")
            }
            principal_grant_indexes = {
                index["name"] for index in inspector.get_indexes("resource_principal_grants")
            }
            group_grant_indexes = {
                index["name"] for index in inspector.get_indexes("resource_group_grants")
            }
            triggers = connection.execute(
                text(
                    """
                    SELECT tgname
                    FROM pg_trigger
                    WHERE NOT tgisinternal AND tgname LIKE 'trg_%_set_updated_at'
                    """
                )
            ).scalars()
            trigger_names = set(triggers)
        assert resource_defaults == ("Restricted", "Optional", None, None)
        assert lease_columns["expires_at"]["nullable"] is True
        assert {"ix_resources_match", "ix_resources_labels_gin"} <= resource_indexes
        assert "ix_group_memberships_principal_id" in membership_indexes
        assert "ix_resource_principal_grants_principal_id" in principal_grant_indexes
        assert "ix_resource_group_grants_group_id" in group_grant_indexes
        assert trigger_names == {
            "trg_groups_set_updated_at",
            "trg_principals_set_updated_at",
            "trg_resources_set_updated_at",
            "trg_resource_secrets_set_updated_at",
        }

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
