"""add player external id mappings"""

import sqlalchemy as sa
from alembic import op

revision = "1a31396d555a"
down_revision = "eee5d8fa4f88"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "external_id_mappings",
        sa.Column("player_id", sa.Uuid(), nullable=True),
        schema="core",
    )

    op.create_foreign_key(
        op.f("fk_external_id_mappings_player_id"),
        "external_id_mappings",
        "players",
        ["player_id"],
        ["id"],
        source_schema="core",
        referent_schema="core",
    )

    op.drop_constraint(
        op.f("external_id_mappings_check"),
        "external_id_mappings",
        schema="core",
        type_="check",
    )

    op.create_check_constraint(
        op.f("external_id_mappings_check"),
        "external_id_mappings",
        """
        num_nonnulls(
            competition_id, season_id, team_id, match_id, player_id
        ) = 1
        AND (
            (entity_type = 'competition' AND competition_id IS NOT NULL)
            OR (entity_type = 'season' AND season_id IS NOT NULL)
            OR (entity_type = 'team' AND team_id IS NOT NULL)
            OR (entity_type = 'match' AND match_id IS NOT NULL)
            OR (entity_type = 'player' AND player_id IS NOT NULL)
        )
        """,
        schema="core",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("external_id_mappings_check"),
        "external_id_mappings",
        schema="core",
        type_="check",
    )

    # Impede a reversão enquanto houver mapeamentos de jogadores.
    # Não referencia player_id, pois a coluna será removida.
    op.create_check_constraint(
        op.f("external_id_mappings_check"),
        "external_id_mappings",
        """
        num_nonnulls(
            competition_id, season_id, team_id, match_id
        ) = 1
        AND (
            (entity_type = 'competition' AND competition_id IS NOT NULL)
            OR (entity_type = 'season' AND season_id IS NOT NULL)
            OR (entity_type = 'team' AND team_id IS NOT NULL)
            OR (entity_type = 'match' AND match_id IS NOT NULL)
        )
        """,
        schema="core",
    )

    op.drop_constraint(
        op.f("fk_external_id_mappings_player_id"),
        "external_id_mappings",
        schema="core",
        type_="foreignkey",
    )

    op.drop_column(
        "external_id_mappings",
        "player_id",
        schema="core",
    )
