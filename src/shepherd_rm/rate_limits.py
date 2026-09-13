"""Enforce distributed request limits using PostgreSQL window counters."""

from __future__ import annotations

import hashlib
import math
import uuid

from sqlalchemy import text

from shepherd_rm.config import Settings
from shepherd_rm.identity.database import identity_transaction


class RateLimitExceeded(Exception):
    """Report how long a caller should wait before another attempt."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


def login_subject(username: str) -> str:
    """Create a stable digest without storing the normalized login name."""
    normalized = username.strip().casefold()
    return hashlib.sha256(normalized.encode()).hexdigest()


def principal_subject(principal_id: uuid.UUID) -> str:
    """Create a stable limiter key for an authenticated principal."""
    return hashlib.sha256(principal_id.bytes).hexdigest()


async def enforce_rate_limit(
    settings: Settings,
    scope: str,
    subject_hash: str,
    allowed_attempts: int,
    window_seconds: int,
) -> None:
    """Atomically count an attempt and reject it when its fixed window is exhausted."""
    cleanup_statement = text(
        """
        DELETE FROM rate_limit_windows
        WHERE ctid IN (
            SELECT ctid
            FROM rate_limit_windows
            WHERE expires_at <= CURRENT_TIMESTAMP
            ORDER BY expires_at
            LIMIT 100
        )
        """
    )
    statement = text(
        """
        INSERT INTO rate_limit_windows (
            scope, subject_hash, window_started_at, expires_at, attempts
        )
        VALUES (
            :scope,
            :subject_hash,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP + make_interval(secs => :window_seconds),
            1
        )
        ON CONFLICT (scope, subject_hash) DO UPDATE SET
            attempts = CASE
                WHEN rate_limit_windows.expires_at <= CURRENT_TIMESTAMP
                THEN 1
                ELSE rate_limit_windows.attempts + 1
            END,
            window_started_at = CASE
                WHEN rate_limit_windows.expires_at <= CURRENT_TIMESTAMP
                THEN CURRENT_TIMESTAMP
                ELSE rate_limit_windows.window_started_at
            END,
            expires_at = CASE
                WHEN rate_limit_windows.expires_at <= CURRENT_TIMESTAMP
                THEN CURRENT_TIMESTAMP + make_interval(secs => :window_seconds)
                ELSE rate_limit_windows.expires_at
            END
        RETURNING attempts,
            EXTRACT(EPOCH FROM (expires_at - CURRENT_TIMESTAMP)) AS retry_after_seconds
        """
    )
    async with identity_transaction(settings) as connection:
        await connection.execute(cleanup_statement)
        row = (
            await connection.execute(
                statement,
                {
                    "scope": scope,
                    "subject_hash": subject_hash,
                    "window_seconds": window_seconds,
                },
            )
        ).one()
    if row.attempts > allowed_attempts:
        raise RateLimitExceeded(max(1, math.ceil(row.retry_after_seconds)))
