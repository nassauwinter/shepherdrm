"""Define fixtures shared only by PostgreSQL-backed integration tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest

from shepherd_rm.application import create_app
from shepherd_rm.config import Settings
from shepherd_rm.database import transaction
from shepherd_rm.identity.authentication import hash_password, issue_token
from shepherd_rm.identity.database import identity_transaction
from tests.integration.support import IdentityEnvironment


@pytest.fixture
def integration_database_url() -> str:
    """Return the integration database URL or skip when it is not configured."""
    database_url = os.getenv("SHEPHERD_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("SHEPHERD_TEST_DATABASE_URL is not configured")
    return database_url


@pytest.fixture
def integration_settings(integration_database_url: str) -> Settings:
    """Build application settings for the configured integration database."""
    return Settings(database_url=integration_database_url)


@pytest.fixture
async def identity_environment(
    integration_settings: Settings,
) -> AsyncIterator[IdentityEnvironment]:
    """Create test-owned identities and clean them up even when setup fails."""
    suffix = uuid.uuid4().hex
    admin_id = uuid.uuid4()
    user_id = uuid.uuid4()
    principals_created = False

    try:
        async with transaction(integration_settings) as connection:
            await connection.execute(
                """
                INSERT INTO principals (id, kind, role, name, display_name)
                VALUES (%s, 'User', 'Admin', %s, 'Test administrator'),
                       (%s, 'User', 'User', %s, 'Test user')
                """,
                (admin_id, f"admin-{suffix}", user_id, f"user-{suffix}"),
            )
            await connection.execute(
                """
                INSERT INTO password_credentials (principal_id, password_hash)
                VALUES (%s, %s), (%s, %s)
                """,
                (
                    admin_id,
                    hash_password("administrator-password"),
                    user_id,
                    hash_password("regular-user-password"),
                ),
            )
        principals_created = True
        async with identity_transaction(integration_settings) as connection:
            admin_token = (await issue_token(connection, admin_id, "test", None)).token
            user_token = (await issue_token(connection, user_id, "test", None)).token

        yield IdentityEnvironment(
            settings=integration_settings,
            suffix=suffix,
            admin_id=admin_id,
            user_id=user_id,
            admin_token=admin_token,
            user_token=user_token,
        )
    finally:
        if principals_created:
            async with transaction(integration_settings) as connection:
                await connection.execute(
                    "DELETE FROM audit_events WHERE subject_type = 'Lease' "
                    "AND subject_id IN ("
                    "SELECT leases.id::text FROM leases JOIN resources "
                    "ON resources.id = leases.resource_id WHERE resources.name LIKE %s)",
                    (f"%-{suffix}",),
                )
                await connection.execute(
                    "DELETE FROM leases WHERE resource_id IN "
                    "(SELECT id FROM resources WHERE name LIKE %s)",
                    (f"%-{suffix}",),
                )
                await connection.execute(
                    "DELETE FROM audit_events "
                    "WHERE actor_id IN (SELECT id FROM principals WHERE name LIKE %s) "
                    "OR actor_id IN (%s, %s)",
                    (f"%-{suffix}", admin_id, user_id),
                )
                await connection.execute(
                    "DELETE FROM resources WHERE name LIKE %s",
                    (f"%-{suffix}",),
                )
                await connection.execute(
                    "DELETE FROM groups WHERE name LIKE %s",
                    (f"%-{suffix}",),
                )
                await connection.execute(
                    "DELETE FROM principals WHERE name LIKE %s",
                    (f"%-{suffix}",),
                )


@pytest.fixture
async def identity_client(
    identity_environment: IdentityEnvironment,
) -> AsyncIterator[httpx.AsyncClient]:
    """Serve the identity API with the test environment's database settings."""
    app = create_app(settings=identity_environment.settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
