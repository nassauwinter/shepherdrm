"""manage timestamps and membership lookup

Revision ID: 898c84cd6b8e
Revises: 7280c6a57e19
Create Date: 2026-08-16 12:30:08.081712
"""

from collections.abc import Sequence

from alembic import op

revision: str = "898c84cd6b8e"
down_revision: str | None = "7280c6a57e19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_group_memberships_principal_id",
        "group_memberships",
        ["principal_id"],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION shepherd_set_updated_at()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.updated_at = CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$
        """
    )
    for table_name in ("principals", "groups", "resources"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_set_updated_at
            BEFORE UPDATE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION shepherd_set_updated_at()
            """
        )


def downgrade() -> None:
    for table_name in ("principals", "groups", "resources"):
        op.execute(f"DROP TRIGGER trg_{table_name}_set_updated_at ON {table_name}")
    op.execute("DROP FUNCTION shepherd_set_updated_at()")
    op.drop_index("ix_group_memberships_principal_id", table_name="group_memberships")
