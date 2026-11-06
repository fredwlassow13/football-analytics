"""create teams"""

import sqlalchemy as sa
from alembic import op

revision = "883f873effb4"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_teams"),
        schema="core",
    )


def downgrade() -> None:
    op.drop_table("teams", schema="core")
