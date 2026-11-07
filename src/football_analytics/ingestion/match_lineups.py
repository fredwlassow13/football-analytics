import argparse
import hashlib
import json
from uuid import UUID, uuid4

import httpx
from sqlalchemy import Engine, text

from football_analytics.infrastructure.database import get_engine
from football_analytics.infrastructure.settings import get_settings
from football_analytics.ingestion.lineups import persist_lineups
from football_analytics.integrations.statsbomb.client import StatsBombClient
from football_analytics.integrations.statsbomb.lineups import normalize_lineups
from football_analytics.integrations.statsbomb.starting_xi import (
    normalize_starting_xi,
)


def store_payload(
    engine: Engine,
    response: httpx.Response,
    run_id: UUID,
) -> object:
    payload: object

    try:
        payload = response.json()
    except ValueError:
        payload = None

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

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import StatsBomb lineups and starter classifications"
    )
    parser.add_argument(
        "--match-id",
        type=int,
        required=True,
        help="External StatsBomb match ID",
    )
    args = parser.parse_args()

    if args.match_id <= 0:
        parser.error("--match-id must be positive")

    engine = get_engine()
    run_id = uuid4()

    with engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO raw.ingestion_runs (
                    id, provider, pipeline_name, status
                )
                VALUES (
                    :id, 'statsbomb', 'match_lineups', 'running'
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
            client = StatsBombClient(http)

            lineup_response = client.fetch_lineups(args.match_id)
            lineup_payload = store_payload(engine, lineup_response, run_id)

            events_response = client.fetch_events(args.match_id)
            events_payload = store_payload(engine, events_response, run_id)

        starting_xi = normalize_starting_xi(events_payload)

        lineups = normalize_lineups(
            lineup_payload,
            args.match_id,
            starting_xi=starting_xi,
        )

        if not lineups:
            raise ValueError("No lineup entries found in the payload")

        with engine.begin() as connection:
            created = persist_lineups(connection, lineups)

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
                    "processed": len(lineups),
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
                    "message": (f"match_id={args.match_id}; {type(exc).__name__}: {exc}"),
                },
            )

        raise

    print(f"Execução: {run_id}")
    print(f"Partida StatsBomb: {args.match_id}")
    print(f"Registros processados: {len(lineups)}")
    print(f"Registros criados: {created}")
    print(f"Registros já existentes: {len(lineups) - created}")
    print(f"Titulares na entrada: {sum(item.is_starter is True for item in lineups)}")
    print(f"Não titulares na entrada: {sum(item.is_starter is False for item in lineups)}")


if __name__ == "__main__":
    main()
