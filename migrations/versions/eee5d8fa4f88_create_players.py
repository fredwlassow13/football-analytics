"""create players"""

import sqlalchemy as sa
from alembic import op

revision = "eee5d8fa4f88"
down_revision = "9799b6b6ce33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "players",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("nickname", sa.Text(), nullable=True),
        sa.Column("nationality", sa.Text(), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_players"),
        ),
        schema="core",
    )


def downgrade() -> None:
    op.drop_table("players", schema="core")
