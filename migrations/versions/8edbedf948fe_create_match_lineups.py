"""create match lineups"""

import sqlalchemy as sa
from alembic import op

revision = "8edbedf948fe"
down_revision = "1a31396d555a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_lineups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("shirt_number", sa.Integer(), nullable=True),
        sa.Column("is_starter", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_match_lineups"),
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["core.matches.id"],
            name=op.f("fk_match_lineups_match_id"),
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["core.teams.id"],
            name=op.f("fk_match_lineups_team_id"),
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["core.players.id"],
            name=op.f("fk_match_lineups_player_id"),
        ),
        sa.UniqueConstraint(
            "match_id",
            "player_id",
            name=op.f("uq_match_lineups_match_player"),
        ),
        sa.CheckConstraint(
            "shirt_number IS NULL OR shirt_number >= 0",
            name=op.f("ck_match_lineups_shirt_number_nonnegative"),
        ),
        schema="core",
    )


def downgrade() -> None:
    op.drop_table("match_lineups", schema="core")
