"""Read-only reconciliation against an explicitly selected stored source payload."""

import argparse
import json
from collections import Counter

from sqlalchemy import create_engine, text

from football_analytics.ingestion.play_phase import build_play_phases
from football_analytics.integrations.statsbomb.event_context import normalize_event_context
from football_analytics.integrations.statsbomb.period_clock import normalize_period_clock
from football_analytics.integrations.statsbomb.play_metadata import normalize_event_play_metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--match-id", required=True, type=int)
    args = parser.parse_args()
    engine = create_engine(args.database_url)
    with engine.connect() as connection:
        source = (
            connection.execute(
                text("""
            SELECT id, payload, sha256 FROM raw.provider_payloads
            WHERE provider = 'statsbomb' AND endpoint LIKE :endpoint
            ORDER BY fetched_at DESC, id DESC LIMIT 1
        """),
                {"endpoint": f"%/events/{args.match_id}.json"},
            )
            .mappings()
            .one()
        )
        payload = source["payload"]
        result = build_play_phases(
            normalize_event_context(payload, match_id=args.match_id),
            normalize_event_play_metadata(payload, match_id=args.match_id),
            normalize_period_clock(payload),
            provider="statsbomb",
            match_id=str(args.match_id),
            events_sha256=source["sha256"],
        )
        rows = (
            connection.execute(
                text("""
            SELECT e->>'id' AS id, CASE
                WHEN e#>>'{play_pattern,name}' IN ('Regular Play', 'From Counter')
                    THEN 'open_play'
                WHEN e#>>'{play_pattern,name}' IN
                    ('From Corner', 'From Free Kick', 'From Throw In',
                     'From Goal Kick', 'From Kick Off') THEN 'set_piece'
                ELSE 'unknown' END AS phase
            FROM raw.provider_payloads p CROSS JOIN LATERAL jsonb_array_elements(p.payload) e
            WHERE p.id = :id AND (e->>'period')::int BETWEEN 1 AND 4
            ORDER BY (e->>'index')::int
        """),
                {"id": source["id"]},
            )
            .mappings()
            .all()
        )
        expected = [(row["id"], row["phase"]) for row in rows]
        actual = [(item.external_event_id, item.phase) for item in result.events]
        if expected != actual:
            raise ValueError("SQL and Python event classifications differ")
        print(
            json.dumps(
                {
                    "match_id": args.match_id,
                    "events_sha256": source["sha256"],
                    "verified_events": len(actual),
                    "phase_counts": dict(Counter(item.phase for item in result.events)),
                    "fragment_compositions": dict(
                        Counter(item.composition for item in result.fragment_phases)
                    ),
                    "excluded_shootout": len(result.excluded_shootout_event_ids),
                },
                indent=2,
            )
        )
    engine.dispose()


if __name__ == "__main__":
    main()
