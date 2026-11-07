import hashlib
import json
from uuid import uuid4

import httpx
from sqlalchemy import text

from football_analytics.infrastructure.database import get_engine
from football_analytics.infrastructure.settings import get_settings
from football_analytics.ingestion.players import persist_players
from football_analytics.integrations.statsbomb.client import StatsBombClient
from football_analytics.integrations.statsbomb.players import normalize_players

FINAL_MATCH_ID = 3869685


def main() -> None:
    engine = get_engine()
    run_id = uuid4()

    with engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO raw.ingestion_runs (
                    id, provider, pipeline_name, status
                )
                VALUES (
                    :id, 'statsbomb', 'final_2022_players', 'running'
                )
            """),
            {"id": run_id},
        )

    try:
        with httpx.Client(
            base_url=get_settings().statsbomb_base_url,
            timeout=httpx.Timeout(15, connect=5),
            follow_redirects=True,
        ) as http:
            response = StatsBombClient(http).fetch_lineups(FINAL_MATCH_ID)

        try:
            payload = response.json()
        except ValueError:
            payload = None

        # Preserva a fonte mesmo se a normalização falhar.
        with engine.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO raw.provider_payloads (
                        id,
                        provider,
                        endpoint,
                        payload,
                        raw_body,
                        sha256,
                        ingestion_run_id
                    )
                    VALUES (
                        :id,
                        'statsbomb',
                        :endpoint,
                        CAST(:payload AS jsonb),
                        :raw_body,
                        :sha256,
                        :run_id
                    )
                """),
                {
                    "id": uuid4(),
                    "endpoint": str(response.url),
                    "payload": json.dumps(payload),
                    "raw_body": response.text,
                    "sha256": hashlib.sha256(response.content).hexdigest(),
                    "run_id": run_id,
                },
            )

        players = normalize_players(payload)

        if not players:
            raise ValueError("No players found in the lineup payload")

        # Cadastros, mapeamentos e conclusão são confirmados juntos.
        with engine.begin() as connection:
            created = persist_players(connection, players)

            connection.execute(
                text("""
                    UPDATE raw.ingestion_runs
                    SET status = 'success',
                        finished_at = now(),
                        records_processed = :processed
                    WHERE id = :run_id
                """),
                {
                    "run_id": run_id,
                    "processed": len(players),
                },
            )

    except Exception as exc:
        with engine.begin() as connection:
            connection.execute(
                text("""
                    UPDATE raw.ingestion_runs
                    SET status = 'failed',
                        finished_at = now(),
                        records_failed = 1
                    WHERE id = :run_id
                """),
                {"run_id": run_id},
            )

            connection.execute(
                text("""
                    INSERT INTO raw.ingestion_errors (
                        id, ingestion_run_id, message
                    )
                    VALUES (:id, :run_id, :message)
                """),
                {
                    "id": uuid4(),
                    "run_id": run_id,
                    "message": f"{type(exc).__name__}: {exc}",
                },
            )

        raise

    print(f"Execução: {run_id}")
    print(f"Jogadores processados: {len(players)}")
    print(f"Jogadores criados: {created}")
    print(f"Jogadores já existentes: {len(players) - created}")


if __name__ == "__main__":
    main()
