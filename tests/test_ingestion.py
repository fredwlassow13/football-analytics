"""Run against a dedicated migrated PostgreSQL via TEST_DATABASE_URL."""

import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text

from football_analytics.ingestion.catalog import ingest_catalog
from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.client import StatsBombClient


def test_persistence_idempotency_and_failed_validation() -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a migrated disposable PostgreSQL database")
    engine = create_engine(url)
    competition = str(uuid4().int % 100000000000)
    entry = {
        "competition_id": int(competition),
        "season_id": 106,
        "competition_name": f"Test {competition}",
        "season_name": "2022",
        "country_name": "International",
        "competition_gender": "male",
    }
    with httpx.Client(
        base_url="https://test.invalid/",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[entry])),
    ) as http:
        first = ingest_catalog(engine, StatsBombClient(http), competition, "106")
        second = ingest_catalog(engine, StatsBombClient(http), competition, "106")
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("""SELECT count(*) FROM core.external_id_mappings
            WHERE provider='statsbomb' AND external_id=:external AND entity_type='competition'"""),
                {"external": competition},
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                text("""SELECT count(*) FROM core.external_id_mappings
            WHERE provider='statsbomb' AND external_id=:external AND entity_type='season'"""),
                {"external": f"{competition}:106"},
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                text("""SELECT count(*) FROM raw.provider_payloads
            WHERE ingestion_run_id IN (:a, :b)"""),
                {"a": first, "b": second},
            ).scalar_one()
            == 2
        )
        assert (
            conn.execute(
                text("""SELECT count(*) FROM raw.ingestion_runs
            WHERE id IN (:a, :b) AND status='success' AND records_processed=1"""),
                {"a": first, "b": second},
            ).scalar_one()
            == 2
        )
    with (
        httpx.Client(
            base_url="https://test.invalid/",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=[{**entry, "competition_name": "Changed"}])
            ),
        ) as http,
        pytest.raises(ValueError, match="metadata"),
    ):
        ingest_catalog(engine, StatsBombClient(http), competition, "106")
    with (
        httpx.Client(
            base_url="https://test.invalid/",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"invalid": True})
            ),
        ) as http,
        pytest.raises(ProviderUnavailable),
    ):
        ingest_catalog(engine, StatsBombClient(http), competition, "106")
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT name FROM core.competitions WHERE name=:name"),
                {"name": entry["competition_name"]},
            ).scalar_one()
            == entry["competition_name"]
        )
        assert (
            conn.execute(
                text("""SELECT count(*) FROM raw.ingestion_errors e
            JOIN raw.ingestion_runs r ON r.id=e.ingestion_run_id
            JOIN raw.provider_payloads p ON p.ingestion_run_id=r.id
            WHERE r.status='failed' AND p.payload = CAST(:payload AS jsonb)"""),
                {"payload": '{"invalid": true}'},
            ).scalar_one()
            >= 1
        )
    engine.dispose()
