"""Create separate schemas for original, normalized and derived data."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for schema in ("raw", "core", "analytics"):
        op.execute(f"CREATE SCHEMA {schema}")


def downgrade() -> None:
    for schema in ("analytics", "core", "raw"):
        op.execute(f"DROP SCHEMA {schema}")
