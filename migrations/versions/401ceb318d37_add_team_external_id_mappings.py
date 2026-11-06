"""add team external id mappings"""

import sqlalchemy as sa
from alembic import op

revision = "401ceb318d37"
down_revision = "883f873effb4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "external_id_mappings",
        sa.Column("team_id", sa.Uuid(), nullable=True),
        schema="core",
    )

    op.create_foreign_key(
        "fk_external_id_mappings_team_id",
        "external_id_mappings",
        "teams",
        ["team_id"],
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


def downgrade() -> None:
    # Impede a reversão enquanto houver vínculos de equipes.
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
        )
        OR (
            entity_type = 'season'
            AND season_id IS NOT NULL
            AND competition_id IS NULL
        )
        """,
        schema="core",
    )

    op.drop_constraint(
        "fk_external_id_mappings_team_id",
        "external_id_mappings",
        schema="core",
        type_="foreignkey",
    )

    op.drop_column(
        "external_id_mappings",
        "team_id",
        schema="core",
    )
