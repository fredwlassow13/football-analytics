import argparse
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import Engine, create_engine, text

from football_analytics.ingestion.audited_event_source import (
    load_audited_event_source,
)
from football_analytics.ingestion.match_lineups import store_payload
from football_analytics.integrations.statsbomb.events import normalize_events
from football_analytics.integrations.statsbomb.period_clock import (
    normalize_period_clock,
)


def import_audited_events(
    engine: Engine,
    *,
    audit_dir: Path,
    match_id: int,
) -> UUID:
    if type(match_id) is not int or match_id <= 0:
        raise ValueError("Match ID must be a positive integer")

    run_id = uuid4()

    with engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO raw.ingestion_runs (
                    id, provider, pipeline_name, status
                )
                VALUES (
                    :id, 'statsbomb', 'audited_match_events', 'running'
                )
            """),
            {"id": run_id},
        )

    try:
        source = load_audited_event_source(
            audit_dir,
            match_id=match_id,
        )

        # Adapt the cached response to the existing storage helper.
        # Constructing Response/Request does not perform an HTTP request.
        response = httpx.Response(
            200,
            content=source.body,
            request=httpx.Request("GET", source.endpoint),
        )

        payload = store_payload(engine, response, run_id)

        events = normalize_events(payload, match_id=match_id)
        normalize_period_clock(payload)

        with engine.begin() as connection:
            connection.execute(
                text("""
                    UPDATE raw.ingestion_runs
                    SET status = 'success',
                        finished_at = now(),
                        records_processed = :processed
                    WHERE id = :id
                """),
                {"id": run_id, "processed": len(events)},
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
                    "message": (f"match_id={match_id}; {type(exc).__name__}: {exc}"),
                },
            )

        raise

    return run_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import previously audited match events without downloading"
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--audit-dir", required=True, type=Path)
    parser.add_argument("--match-id", required=True, type=int)
    args = parser.parse_args()

    if args.match_id <= 0:
        parser.error("--match-id must be positive")

    engine = create_engine(args.database_url)

    try:
        run_id = import_audited_events(
            engine,
            audit_dir=args.audit_dir,
            match_id=args.match_id,
        )

        with engine.connect() as connection:
            row = (
                connection.execute(
                    text("""
                    SELECT
                        p.id AS payload_id,
                        p.sha256,
                        r.records_processed
                    FROM raw.provider_payloads p
                    JOIN raw.ingestion_runs r
                        ON r.id = p.ingestion_run_id
                    WHERE r.id = :id
                """),
                    {"id": run_id},
                )
                .mappings()
                .one()
            )

        print(
            json.dumps(
                {
                    "run_id": str(run_id),
                    "match_id": args.match_id,
                    "payload_id": str(row["payload_id"]),
                    "events_sha256": row["sha256"],
                    "records_processed": row["records_processed"],
                },
                indent=2,
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
