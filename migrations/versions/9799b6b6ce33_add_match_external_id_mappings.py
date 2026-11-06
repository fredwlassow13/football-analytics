"""add match external id mappings"""

import sqlalchemy as sa
from alembic import op

revision = "9799b6b6ce33"
down_revision = "bb0c5243a6da"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "external_id_mappings",
        sa.Column("match_id", sa.Uuid(), nullable=True),
        schema="core",
    )

    op.create_foreign_key(
        op.f("fk_external_id_mappings_match_id"),
        "external_id_mappings",
        "matches",
        ["match_id"],
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
        (
            entity_type = 'competition'
            AND competition_id IS NOT NULL
            AND season_id IS NULL
            AND team_id IS NULL
            AND match_id IS NULL
        )
        OR (
            entity_type = 'season'
            AND season_id IS NOT NULL
            AND competition_id IS NULL
            AND team_id IS NULL
            AND match_id IS NULL
        )
        OR (
            entity_type = 'team'
            AND team_id IS NOT NULL
            AND competition_id IS NULL
            AND season_id IS NULL
            AND match_id IS NULL
        )
        OR (
            entity_type = 'match'
            AND match_id IS NOT NULL
            AND competition_id IS NULL
            AND season_id IS NULL
            AND team_id IS NULL
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

    # Bloqueia a reversão se houver mapeamentos de partidas.
    op.create_check_constraint(
        op.f("external_id_mappings_check"),
        "external_id_mappings",
        """
        (
            entity_type = 'competition'
            AND competition_id IS NOT NULL
            AND season_id IS NULL
            AND team_id IS NULL
        )
        OR (
            entity_type = 'season'
            AND season_id IS NOT NULL
            AND competition_id IS NULL
            AND team_id IS NULL
        )
        OR (
            entity_type = 'team'
            AND team_id IS NOT NULL
            AND competition_id IS NULL
            AND season_id IS NULL
        )
        """,
        schema="core",
    )

    op.drop_constraint(
        op.f("fk_external_id_mappings_match_id"),
        "external_id_mappings",
        schema="core",
        type_="foreignkey",
    )

    op.drop_column(
        "external_id_mappings",
        "match_id",
        schema="core",
    )
