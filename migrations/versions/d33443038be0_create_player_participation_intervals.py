"""create player participation intervals"""

import sqlalchemy as sa
from alembic import op

revision = "d33443038be0"
down_revision = "e31e524522ef"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_participation_intervals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("participation_id", sa.Uuid(), nullable=False),
        sa.Column("interval_number", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_player_participation_intervals"),
        ),
        sa.ForeignKeyConstraint(
            ["participation_id"],
            ["analytics.player_match_participation.id"],
            name=op.f("fk_participation_intervals_result"),
        ),
        sa.UniqueConstraint(
            "participation_id",
            "interval_number",
            name=op.f("uq_participation_intervals_result_number"),
        ),
        sa.CheckConstraint(
            "interval_number > 0",
            name=op.f("ck_participation_intervals_number_positive"),
        ),
        sa.CheckConstraint(
            "start_ms >= 0",
            name=op.f("ck_participation_intervals_start_nonnegative"),
        ),
        sa.CheckConstraint(
            "end_ms >= start_ms",
            name=op.f("ck_participation_intervals_end_order"),
        ),
        schema="analytics",
    )


def downgrade() -> None:
    op.drop_table(
        "player_participation_intervals",
        schema="analytics",
    )
