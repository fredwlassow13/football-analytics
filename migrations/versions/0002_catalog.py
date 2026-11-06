"""Catalog foundation and append-only ingestion audit."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE raw.ingestion_runs (
        id uuid PRIMARY KEY, provider text NOT NULL, pipeline_name text NOT NULL,
        started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
        status text NOT NULL CHECK (status IN ('running', 'success', 'failed')),
        records_processed integer NOT NULL DEFAULT 0,
        records_failed integer NOT NULL DEFAULT 0)""")
    op.execute("""CREATE TABLE raw.provider_payloads (
        id uuid PRIMARY KEY, provider text NOT NULL, endpoint text NOT NULL,
        payload jsonb, raw_body text NOT NULL, sha256 text NOT NULL,
        fetched_at timestamptz NOT NULL DEFAULT now(),
        ingestion_run_id uuid NOT NULL REFERENCES raw.ingestion_runs(id))""")
    op.execute("""CREATE TABLE raw.ingestion_errors (
        id uuid PRIMARY KEY, ingestion_run_id uuid NOT NULL REFERENCES raw.ingestion_runs(id),
        message text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE core.competitions (
        id uuid PRIMARY KEY, name text NOT NULL, country_name text NOT NULL,
        gender text NOT NULL)""")
    op.execute("""CREATE TABLE core.seasons (
        id uuid PRIMARY KEY, competition_id uuid NOT NULL REFERENCES core.competitions(id),
        name text NOT NULL, UNIQUE (competition_id, name))""")
    op.execute("""CREATE TABLE core.external_id_mappings (
        id uuid PRIMARY KEY, provider text NOT NULL, entity_type text NOT NULL,
        external_id text NOT NULL,
        competition_id uuid REFERENCES core.competitions(id),
        season_id uuid REFERENCES core.seasons(id),
        UNIQUE (provider, entity_type, external_id),
        CHECK ((entity_type = 'competition' AND competition_id IS NOT NULL AND season_id IS NULL)
          OR (entity_type = 'season' AND season_id IS NOT NULL AND competition_id IS NULL)))""")


def downgrade() -> None:
    for table in (
        "core.external_id_mappings",
        "core.seasons",
        "core.competitions",
        "raw.ingestion_errors",
        "raw.provider_payloads",
        "raw.ingestion_runs",
    ):
        op.execute(f"DROP TABLE {table}")
