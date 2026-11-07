import argparse
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text

from football_analytics.infrastructure.database import get_engine
from football_analytics.infrastructure.settings import get_settings
from football_analytics.ingestion.match_lineups import store_payload
from football_analytics.ingestion.match_participation import (
    reconstruct_match_participation,
)
from football_analytics.ingestion.participation_storage import (
    get_or_create_calculation,
    persist_participation_results,
)
from football_analytics.ingestion.team_occupancy import build_team_occupancy
from football_analytics.integrations.statsbomb.client import StatsBombClient
from football_analytics.integrations.statsbomb.period_clock import (
    normalize_period_clock,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate and store player participation")
    parser.add_argument("--match-id", type=int, required=True)
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
                    :id, 'statsbomb', 'match_participation', 'running'
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

        players = reconstruct_match_participation(
            lineup_payload,
            events_payload,
            args.match_id,
        )

        periods = normalize_period_clock(events_payload)
        duration = periods[-1].end_elapsed_ms

        build_team_occupancy(players, duration)

        expected_results = len(players)
        expected_intervals = sum(len(player.intervals) for player in players)

        with engine.begin() as connection:
            match_id: UUID | None = connection.execute(
                text("""
                    SELECT match_id
                    FROM core.external_id_mappings
                    WHERE provider = 'statsbomb'
                      AND entity_type = 'match'
                      AND external_id = :external_id
                """),
                {"external_id": str(args.match_id)},
            ).scalar_one_or_none()

            if match_id is None:
                raise ValueError("Stored match mapping not found")

            lineups_payload_id: UUID = connection.execute(
                text("""
                    SELECT id
                    FROM raw.provider_payloads
                    WHERE ingestion_run_id = :run_id
                      AND endpoint = :endpoint
                """),
                {
                    "run_id": run_id,
                    "endpoint": str(lineup_response.url),
                },
            ).scalar_one()

            events_payload_id: UUID = connection.execute(
                text("""
                    SELECT id
                    FROM raw.provider_payloads
                    WHERE ingestion_run_id = :run_id
                      AND endpoint = :endpoint
                """),
                {
                    "run_id": run_id,
                    "endpoint": str(events_response.url),
                },
            ).scalar_one()

            calculation_id, created = get_or_create_calculation(
                connection,
                match_id,
                lineups_payload_id,
                events_payload_id,
                run_id,
            )

            if created:
                result_count, interval_count = persist_participation_results(
                    connection,
                    calculation_id,
                    players,
                    match_duration_ms=duration,
                )

            else:
                stored_results: int = connection.execute(
                    text("""
                        SELECT count(*)
                        FROM analytics.player_match_participation
                        WHERE calculation_id = :id
                    """),
                    {"id": calculation_id},
                ).scalar_one()

                stored_intervals: int = connection.execute(
                    text("""
                        SELECT count(*)
                        FROM analytics.player_participation_intervals AS i
                        JOIN analytics.player_match_participation AS p
                            ON p.id = i.participation_id
                        WHERE p.calculation_id = :id
                    """),
                    {"id": calculation_id},
                ).scalar_one()

                if stored_results != expected_results or stored_intervals != expected_intervals:
                    raise ValueError("Existing calculation has incompatible record counts")

                result_count = 0
                interval_count = 0

            connection.execute(
                text("""
                    UPDATE raw.ingestion_runs
                    SET status = 'success',
                        finished_at = now(),
                        records_processed = :processed
                    WHERE id = :id
                """),
                {
                    "id": run_id,
                    "processed": expected_results,
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
                    WHERE id = :id
                """),
                {"id": run_id},
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
    print(f"Cálculo: {calculation_id}")
    print(f"Cálculo novo: {created}")
    print(f"Jogadores processados: {expected_results}")
    print(f"Novos resultados: {result_count}")
    print(f"Novos intervalos: {interval_count}")


if __name__ == "__main__":
    main()
