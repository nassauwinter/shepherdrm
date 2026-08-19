"""Append safe domain audit events within caller-managed transactions."""

from __future__ import annotations

import uuid

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from shepherd_rm.database_models import AuditEvent
from shepherd_rm.request_context import get_correlation_id


async def record_audit_event(
    connection: AsyncConnection,
    actor_id: uuid.UUID,
    action: str,
    subject_type: str,
    subject_id: uuid.UUID,
    metadata: dict[str, str] | None = None,
) -> None:
    """Append a safe, caller-supplied audit event in the domain transaction."""
    await connection.execute(
        insert(AuditEvent).values(
            actor_id=actor_id,
            action=action,
            subject_type=subject_type,
            subject_id=str(subject_id),
            correlation_id=get_correlation_id(),
            metadata_=metadata or {},
        )
    )
