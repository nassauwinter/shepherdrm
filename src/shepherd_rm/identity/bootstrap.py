"""Provide the concurrency-safe command for creating the first administrator."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import uuid

from shepherd_rm.config import Settings, get_settings
from shepherd_rm.database import transaction
from shepherd_rm.identity.authentication import hash_password_async
from shepherd_rm.identity.constants import ADMINISTRATION_LOCK_ID


async def bootstrap_administrator(
    username: str,
    display_name: str,
    password: str,
    settings: Settings | None = None,
) -> uuid.UUID:
    """Create the first administrator exactly once across concurrent processes."""
    current_settings = settings or get_settings()
    async with transaction(current_settings) as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(%s)", (ADMINISTRATION_LOCK_ID,))
        cursor = await connection.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM principals AS principal
                JOIN password_credentials AS credential
                  ON credential.principal_id = principal.id
                WHERE principal.kind = 'User'
                  AND principal.role = 'Admin'
                  AND principal.archived_at IS NULL
            )
            """
        )
        row = await cursor.fetchone()
        assert row is not None
        if row[0]:
            raise RuntimeError("an administrator already exists")

        principal_id = uuid.uuid4()
        await connection.execute(
            """
            INSERT INTO principals (id, kind, role, name, display_name)
            VALUES (%s, 'User', 'Admin', %s, %s)
            """,
            (principal_id, username, display_name),
        )
        await connection.execute(
            "INSERT INTO password_credentials (principal_id, password_hash) VALUES (%s, %s)",
            (principal_id, await hash_password_async(password)),
        )
        await connection.execute(
            """
            INSERT INTO audit_events
                (actor_id, action, subject_type, subject_id, correlation_id, metadata)
            VALUES (NULL, 'administrator.bootstrapped', 'Principal', %s, 'bootstrap', '{}'::jsonb)
            """,
            (str(principal_id),),
        )
    return principal_id


def run() -> None:
    parser = argparse.ArgumentParser(description="Create the first Shepherd RM administrator")
    parser.add_argument("username")
    parser.add_argument("--display-name")
    arguments = parser.parse_args()
    password = getpass.getpass("Administrator password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    if len(password) < 12:
        raise SystemExit("Password must contain at least 12 characters")
    try:
        principal_id = asyncio.run(
            bootstrap_administrator(
                arguments.username,
                arguments.display_name or arguments.username,
                password,
            )
        )
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    print(f"Created administrator {principal_id}")
