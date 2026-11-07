"""create participation calculations"""

import sqlalchemy as sa
from alembic import op

revision = "7c44b832d801"
down_revision = "8edbedf948fe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "participation_calculations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("formula_version", sa.Text(), nullable=False),
        sa.Column(
            "lineups_payload_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "events_payload_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "lineups_sha256",
            sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "events_sha256",
            sa.String(64),
            nullable=False,
        ),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_participation_calculations"),
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["core.matches.id"],
            name=op.f("fk_participation_calculations_match"),
        ),
        sa.ForeignKeyConstraint(
            ["lineups_payload_id"],
            ["raw.provider_payloads.id"],
            name=op.f("fk_participation_calculations_lineups"),
        ),
        sa.ForeignKeyConstraint(
            ["events_payload_id"],
            ["raw.provider_payloads.id"],
            name=op.f("fk_participation_calculations_events"),
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["raw.ingestion_runs.id"],
            name=op.f("fk_participation_calculations_run"),
        ),
        sa.UniqueConstraint(
            "match_id",
            "metric_name",
            "formula_version",
            "lineups_sha256",
            "events_sha256",
            name=op.f("uq_participation_calculations_source_version"),
        ),
        sa.CheckConstraint(
            "length(lineups_sha256) = 64 AND length(events_sha256) = 64",
            name=op.f("ck_participation_calculations_hash_lengths"),
        ),
        schema="analytics",
    )


def downgrade() -> None:
    op.drop_table(
        "participation_calculations",
        schema="analytics",
    )
