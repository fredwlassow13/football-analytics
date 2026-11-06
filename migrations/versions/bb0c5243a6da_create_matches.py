"""create matches"""

import sqlalchemy as sa
from alembic import op

revision = "bb0c5243a6da"
down_revision = "401ceb318d37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("home_team_id", sa.Uuid(), nullable=False),
        sa.Column("away_team_id", sa.Uuid(), nullable=False),
        sa.Column("match_date", sa.Date(), nullable=False),
        sa.Column(
            "kickoff_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_matches"),
        ),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["core.seasons.id"],
            name=op.f("fk_matches_season_id"),
        ),
        sa.ForeignKeyConstraint(
            ["home_team_id"],
            ["core.teams.id"],
            name=op.f("fk_matches_home_team_id"),
        ),
        sa.ForeignKeyConstraint(
            ["away_team_id"],
            ["core.teams.id"],
            name=op.f("fk_matches_away_team_id"),
        ),
        sa.CheckConstraint(
            "home_team_id <> away_team_id",
            name=op.f("ck_matches_different_teams"),
        ),
        sa.CheckConstraint(
            "home_score IS NULL OR home_score >= 0",
            name=op.f("ck_matches_home_score_nonnegative"),
        ),
        sa.CheckConstraint(
            "away_score IS NULL OR away_score >= 0",
            name=op.f("ck_matches_away_score_nonnegative"),
        ),
        schema="core",
    )

    op.create_index(
        op.f("ix_matches_season_id"),
        "matches",
        ["season_id"],
        schema="core",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_matches_season_id"),
        table_name="matches",
        schema="core",
    )

    op.drop_table("matches", schema="core")
