"""Explicit CLI ingestion; GET endpoints never trigger database writes."""

import argparse
import hashlib
import json
from uuid import UUID, uuid4

import httpx
from sqlalchemy import Engine, text

from football_analytics.infrastructure.database import get_engine
from football_analytics.infrastructure.settings import get_settings
from football_analytics.ingestion.locks import CORE_INGESTION_LOCK_ID
from football_analytics.integrations.statsbomb.client import StatsBombClient


def ingest_catalog(engine: Engine, client: StatsBombClient, competition: str, season: str) -> UUID:
    run_id = uuid4()
    with engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO raw.ingestion_runs
            (id, provider, pipeline_name, status)
            VALUES (:id, 'statsbomb', 'catalog', 'running')"""),
            {"id": run_id},
        )
    try:
        response = client.fetch_catalog()
        # Persist before validation, so invalid provider responses remain inspectable.
        try:
            payload = response.json()
        except ValueError:
            payload = None
        with engine.begin() as conn:
            conn.execute(
                text("""INSERT INTO raw.provider_payloads
                (id, provider, endpoint, payload, raw_body, sha256, ingestion_run_id)
                VALUES (:id, 'statsbomb', :endpoint, CAST(:payload AS jsonb),
                        :body, :sha, :run)"""),
                {
                    "id": uuid4(),
                    "endpoint": str(response.url),
                    "payload": json.dumps(payload),
                    "body": response.text,
                    "sha": hashlib.sha256(response.content).hexdigest(),
                    "run": run_id,
                },
            )
        entries = client.normalize_catalog(payload)
        selected = [
            e
            for e in entries
            if e.external_competition_id == competition and e.external_season_id == season
        ]
        if len(selected) != 1:
            raise ValueError("Expected exactly one competition/season pair in provider catalog")
        entry = selected[0]
        with engine.begin() as conn:
            # Serialize catalog writers across processes, including first-time inserts.
            conn.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": CORE_INGESTION_LOCK_ID}
            )
            competition_id = conn.execute(
                text("""SELECT competition_id
                FROM core.external_id_mappings WHERE provider='statsbomb'
                AND entity_type='competition' AND external_id=:external"""),
                {"external": competition},
            ).scalar_one_or_none()
            if competition_id is None:
                competition_id = uuid4()
                conn.execute(
                    text("""INSERT INTO core.competitions (id, name, country_name, gender)
                    VALUES (:id, :name, :country, :gender)"""),
                    {
                        "id": competition_id,
                        "name": entry.competition_name,
                        "country": entry.country_name,
                        "gender": entry.gender,
                    },
                )
                conn.execute(
                    text("""INSERT INTO core.external_id_mappings
                    (id, provider, entity_type, external_id, competition_id)
                    VALUES (:id, 'statsbomb', 'competition', :external, :internal)"""),
                    {"id": uuid4(), "external": competition, "internal": competition_id},
                )
            else:
                existing = conn.execute(
                    text("""SELECT name, country_name, gender
                    FROM core.competitions WHERE id=:id"""),
                    {"id": competition_id},
                ).one()
                if tuple(existing) != (entry.competition_name, entry.country_name, entry.gender):
                    raise ValueError("Provider changed competition metadata; review raw payload")
            # Season IDs are scoped to competition; some providers reuse season IDs.
            external_season = f"{competition}:{season}"
            season_id = conn.execute(
                text("""SELECT season_id FROM core.external_id_mappings
                WHERE provider='statsbomb' AND entity_type='season' AND external_id=:external"""),
                {"external": external_season},
            ).scalar_one_or_none()
            if season_id is None:
                season_id = uuid4()
                conn.execute(
                    text("""INSERT INTO core.seasons (id, competition_id, name)
                    VALUES (:id, :competition, :name)"""),
                    {"id": season_id, "competition": competition_id, "name": entry.season_name},
                )
                conn.execute(
                    text("""INSERT INTO core.external_id_mappings
                    (id, provider, entity_type, external_id, season_id)
                    VALUES (:id, 'statsbomb', 'season', :external, :internal)"""),
                    {"id": uuid4(), "external": external_season, "internal": season_id},
                )
            else:
                existing_season = conn.execute(
                    text("SELECT competition_id, name FROM core.seasons WHERE id=:id"),
                    {"id": season_id},
                ).one()
                if tuple(existing_season) != (competition_id, entry.season_name):
                    raise ValueError("Provider changed season metadata; review raw payload")
            conn.execute(
                text("""UPDATE raw.ingestion_runs SET status='success',
                finished_at=now(), records_processed=1 WHERE id=:id"""),
                {"id": run_id},
            )
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(
                text("""UPDATE raw.ingestion_runs SET status='failed',
                finished_at=now(), records_failed=1 WHERE id=:id"""),
                {"id": run_id},
            )
            conn.execute(
                text("""INSERT INTO raw.ingestion_errors (id, ingestion_run_id, message)
                VALUES (:id, :run, :message)"""),
                {"id": uuid4(), "run": run_id, "message": f"{type(exc).__name__}: {exc}"},
            )
        raise
    return run_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest one StatsBomb competition/season")
    parser.add_argument("--competition", required=True)
    parser.add_argument("--season", required=True)
    args = parser.parse_args()
    with httpx.Client(
        base_url=get_settings().statsbomb_base_url,
        timeout=httpx.Timeout(15, connect=5),
        follow_redirects=True,
    ) as http:
        run_id = ingest_catalog(get_engine(), StatsBombClient(http), args.competition, args.season)
    print(json.dumps({"run_id": str(run_id), "status": "success"}))


if __name__ == "__main__":
    main()
