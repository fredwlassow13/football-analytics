"""create player match participation"""

import sqlalchemy as sa
from alembic import op

revision = "e31e524522ef"
down_revision = "7c44b832d801"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Permitem referências compostas que validam a partida.
    op.create_unique_constraint(
        op.f("uq_participation_calculations_id_match"),
        "participation_calculations",
        ["id", "match_id"],
        schema="analytics",
    )

    op.create_unique_constraint(
        op.f("uq_match_lineups_id_match"),
        "match_lineups",
        ["id", "match_id"],
        schema="core",
    )

    op.create_table(
        "player_match_participation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("calculation_id", sa.Uuid(), nullable=False),
        sa.Column("lineup_id", sa.Uuid(), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("is_starter", sa.Boolean(), nullable=True),
        sa.Column(
            "has_participation_evidence",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "elapsed_on_field_ms",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_player_match_participation"),
        ),
        sa.ForeignKeyConstraint(
            ["calculation_id", "match_id"],
            [
                "analytics.participation_calculations.id",
                "analytics.participation_calculations.match_id",
            ],
            name=op.f("fk_player_participation_calculation_match"),
        ),
        sa.ForeignKeyConstraint(
            ["lineup_id", "match_id"],
            [
                "core.match_lineups.id",
                "core.match_lineups.match_id",
            ],
            name=op.f("fk_player_participation_lineup_match"),
        ),
        sa.UniqueConstraint(
            "calculation_id",
            "lineup_id",
            name=op.f("uq_player_participation_calculation_lineup"),
        ),
        sa.CheckConstraint(
            "elapsed_on_field_ms IS NULL OR elapsed_on_field_ms >= 0",
            name=op.f("ck_player_participation_duration_nonnegative"),
        ),
        sa.CheckConstraint(
            """
            (
                has_participation_evidence
                AND elapsed_on_field_ms IS NOT NULL
            )
            OR (
                NOT has_participation_evidence
                AND elapsed_on_field_ms IS NULL
            )
            """,
            name=op.f("ck_player_participation_evidence_duration"),
        ),
        sa.CheckConstraint(
            "is_starter IS NOT TRUE OR has_participation_evidence",
            name=op.f("ck_player_participation_starter_evidence"),
        ),
        schema="analytics",
    )


def downgrade() -> None:
    op.drop_table(
        "player_match_participation",
        schema="analytics",
    )

    op.drop_constraint(
        op.f("uq_match_lineups_id_match"),
        "match_lineups",
        schema="core",
        type_="unique",
    )

    op.drop_constraint(
        op.f("uq_participation_calculations_id_match"),
        "participation_calculations",
        schema="analytics",
        type_="unique",
    )
