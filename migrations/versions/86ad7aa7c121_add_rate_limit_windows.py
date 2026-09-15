"""Add durable distributed rate-limit counters.

Revision ID: 86ad7aa7c121
Revises: 4c7d9e2a6b31
Create Date: 2026-09-13 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "86ad7aa7c121"
down_revision: str | None = "4c7d9e2a6b31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the fixed-window rate-limit counter table."""
    op.create_table(
        "rate_limit_windows",
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("subject_hash", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.CheckConstraint("attempts > 0", name=op.f("ck_rate_limit_windows_attempts_positive")),
        sa.CheckConstraint(
            "expires_at > window_started_at",
            name=op.f("ck_rate_limit_windows_expiration_after_start"),
        ),
        sa.PrimaryKeyConstraint("scope", "subject_hash", name=op.f("pk_rate_limit_windows")),
    )
    op.create_index(
        "ix_rate_limit_windows_expires_at",
        "rate_limit_windows",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove durable rate-limit counters."""
    op.execute("DROP INDEX IF EXISTS ix_rate_limit_windows_expires_at")
    op.drop_table("rate_limit_windows")
