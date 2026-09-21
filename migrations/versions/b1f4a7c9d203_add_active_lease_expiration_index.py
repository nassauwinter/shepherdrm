"""Index active finite leases for expiration work and metrics.

Revision ID: b1f4a7c9d203
Revises: 86ad7aa7c121
Create Date: 2026-09-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1f4a7c9d203"
down_revision: str | None = "86ad7aa7c121"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Index only unended leases that have an expiration time."""
    op.create_index(
        "ix_leases_active_expiration",
        "leases",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("ended_at IS NULL AND expires_at IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove the active finite-lease expiration index."""
    op.drop_index("ix_leases_active_expiration", table_name="leases")
