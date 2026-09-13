"""Add encrypted and externally referenced resource secrets.

Revision ID: 4c7d9e2a6b31
Revises: 7280c6a57e19
Create Date: 2026-09-02 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4c7d9e2a6b31"
down_revision: str | None = "7280c6a57e19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the constrained resource-secret storage table."""
    op.create_table(
        "resource_secrets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("resource_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("encrypted_value", sa.LargeBinary(), nullable=True),
        sa.Column("encryption_key_id", sa.String(length=255), nullable=True),
        sa.Column("external_provider", sa.String(length=255), nullable=True),
        sa.Column("external_reference", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(mode = 'Managed' AND encrypted_value IS NOT NULL "
            "AND encryption_key_id IS NOT NULL AND external_provider IS NULL "
            "AND external_reference IS NULL) OR "
            "(mode = 'External' AND encrypted_value IS NULL "
            "AND encryption_key_id IS NULL AND external_provider IS NOT NULL "
            "AND external_reference IS NOT NULL)",
            name=op.f("ck_resource_secrets_material_matches_mode"),
        ),
        sa.CheckConstraint(
            "mode IN ('Managed', 'External')",
            name=op.f("ck_resource_secrets_mode_allowed"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_resource_secrets_version_positive")),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["resources.id"],
            name=op.f("fk_resource_secrets_resource_id_resources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_secrets")),
        sa.UniqueConstraint("resource_id", "name", name="uq_resource_secrets_resource_name"),
    )
    op.create_index(
        op.f("ix_resource_secrets_resource_id"),
        "resource_secrets",
        ["resource_id"],
        unique=False,
    )
    op.execute(
        """
        CREATE TRIGGER trg_resource_secrets_set_updated_at
        BEFORE UPDATE ON resource_secrets
        FOR EACH ROW
        EXECUTE FUNCTION shepherd_set_updated_at()
        """
    )


def downgrade() -> None:
    """Remove resource-secret storage and its timestamp trigger."""
    op.execute("DROP TRIGGER trg_resource_secrets_set_updated_at ON resource_secrets")
    op.drop_index(op.f("ix_resource_secrets_resource_id"), table_name="resource_secrets")
    op.drop_table("resource_secrets")
