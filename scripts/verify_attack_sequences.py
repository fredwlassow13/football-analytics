"""Read-only SQL reconciliation of attack boundaries and source coverage."""

import argparse
import json
from collections import Counter
from decimal import Decimal

from sqlalchemy import create_engine, text

from football_analytics.ingestion.attack_sequences import START_TYPES, build_attack_sequences
from football_analytics.integrations.base import MatchFoulCommitted, MatchFoulWon
from football_analytics.integrations.statsbomb.event_context import normalize_event_context
from football_analytics.integrations.statsbomb.passes import normalize_passes
from football_analytics.integrations.statsbomb.period_clock import normalize_period_clock
from football_analytics.integrations.statsbomb.play_metadata import normalize_event_play_metadata
from football_analytics.integrations.statsbomb.pressures_and_fouls import (
    normalize_pressures_and_fouls,
)
from football_analytics.integrations.statsbomb.shots import normalize_shots

SQL = """
WITH events AS (
    SELECT e FROM raw.provider_payloads p,
    LATERAL jsonb_array_elements(p.payload) e
    WHERE p.id = :id AND (e->>'period')::int BETWEEN 1 AND 4
), marked AS (
    SELECT e, CASE
        WHEN e#>>'{shot,outcome,name}' = 'Goal'
             OR e#>>'{type,name}' IN ('Own Goal For', 'Own Goal Against') THEN 'goal'
        WHEN (e->>'out')::boolean IS TRUE OR e#>>'{pass,outcome,name}' = 'Out' THEN 'out'
        WHEN e#>>'{type,name}' = 'Offside'
             OR e#>>'{pass,outcome,name}' = 'Pass Offside' THEN 'offside'
        WHEN e#>>'{type,name}' IN ('Foul Committed', 'Foul Won')
             AND COALESCE((e#>>'{foul_committed,advantage}')::boolean,
                          (e#>>'{foul_won,advantage}')::boolean, false) IS FALSE
             AND NOT EXISTS (
                 SELECT 1 FROM events linked WHERE
                 linked.e->>'id' IN (
                     SELECT jsonb_array_elements_text(COALESCE(events.e->'related_events','[]'))
                 ) AND (linked.e#>>'{foul_committed,advantage}' = 'true'
                     OR linked.e#>>'{foul_won,advantage}' = 'true')
             ) THEN 'foul'
        WHEN e#>>'{type,name}' = 'Injury Stoppage' THEN 'injury_stoppage'
    END AS reason FROM events
), boundaries AS (
    SELECT e, reason FROM marked WHERE reason IS NOT NULL
    UNION ALL
    SELECT e, 'restart' FROM events WHERE
        e#>>'{pass,type,name}' IN ('Corner','Free Kick','Throw-in','Goal Kick','Kick Off')
        OR e#>>'{shot,type,name}' IN ('Corner','Free Kick','Penalty','Kick Off')
)
SELECT e->>'id' AS id, reason,
    EXTRACT(EPOCH FROM (e->>'timestamp')::time) * 1000 +
    CASE WHEN reason IN ('out','goal','offside') AND e#>>'{type,name}' IN ('Pass','Shot')
        THEN (e->>'duration')::numeric * 1000 ELSE 0 END AS effective
FROM boundaries ORDER BY (e->>'index')::int, reason
"""


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
        result = build_attack_sequences(
            normalize_event_context(payload, match_id=args.match_id),
            normalize_event_play_metadata(payload, match_id=args.match_id),
            normalize_passes(payload, match_id=args.match_id),
            normalize_shots(payload, match_id=args.match_id),
            [
                item
                for item in normalize_pressures_and_fouls(payload, match_id=args.match_id)
                if isinstance(item, MatchFoulCommitted | MatchFoulWon)
            ],
            normalize_period_clock(payload),
            provider="statsbomb",
            match_id=str(args.match_id),
            events_sha256=source["sha256"],
        )
        expected = sorted(
            (row["id"], row["reason"], row["effective"])
            for row in connection.execute(text(SQL), {"id": source["id"]}).mappings()
        )
        actual = sorted(
            (item.external_event_id, item.reason, item.effective_timestamp_ms)
            for item in result.boundary_evidence
        )
        if expected != actual:
            raise ValueError(f"Boundary SQL mismatch: {set(expected) ^ set(actual)}")
        assigned = [key for attack in result.attacks for key in attack.external_event_ids]
        if (
            len(assigned) != len(set(assigned))
            or set(assigned).intersection(result.unassigned_event_ids)
            or set(assigned).union(result.unassigned_event_ids) != set(result.external_event_ids)
        ):
            raise ValueError("Attack source population mismatch")
        raw_by_id = {item["id"]: item for item in payload}
        terminal_ids = {
            item.external_event_id for item in result.boundary_evidence if item.reason != "restart"
        }
        restart_ids = {
            item.external_event_id for item in result.boundary_evidence if item.reason == "restart"
        }
        for attack in result.attacks:
            records = [raw_by_id[key] for key in attack.external_event_ids]
            if [r["index"] for r in records] != sorted(r["index"] for r in records):
                raise ValueError("Attack order mismatch")
            if any(r["period"] != attack.period for r in records):
                raise ValueError("Attack crosses periods")
            if any(
                r["type"]["name"] in START_TYPES
                and str(r.get("team", {}).get("id")) != attack.external_team_id
                for r in records
            ):
                raise ValueError("Attack crosses controlled action teams")
            if terminal_ids.intersection(attack.external_event_ids[:-1]):
                raise ValueError("Attack crosses a stop")
            if restart_ids.intersection(attack.external_event_ids[1:]):
                raise ValueError("Attack crosses a restart")
        playing_shots = {
            item["id"] for item in payload if item["period"] != 5 and item["type"]["name"] == "Shot"
        }
        assigned_shots = {key for attack in result.attacks for key in attack.shot_event_ids}
        if playing_shots != assigned_shots:
            raise ValueError("Shot attack coverage mismatch")
        print(
            json.dumps(
                {
                    "match_id": args.match_id,
                    "events_sha256": source["sha256"],
                    "verified_boundary_records": len(expected),
                    "source_events": len(result.external_event_ids),
                    "assigned_events": len(assigned),
                    "unassigned_events": len(result.unassigned_event_ids),
                    "attacks": len(result.attacks),
                    "assigned_shots": len(assigned_shots),
                    "attack_issues": dict(Counter(i for a in result.attacks for i in a.issues)),
                    "attack_status": dict(Counter(a.status for a in result.attacks)),
                    "attack_start_reasons": dict(Counter(a.start_reason for a in result.attacks)),
                    "attack_end_reasons": dict(Counter(a.end_reason for a in result.attacks)),
                    "boundary_reasons": dict(Counter(e.reason for e in result.boundary_evidence)),
                    "stoppages": len(result.stoppages),
                    "stoppage_status": dict(Counter(s.status for s in result.stoppages)),
                    "stoppage_issues": dict(Counter(i for s in result.stoppages for i in s.issues)),
                    "bounded_stoppage_window_ms": str(
                        sum(
                            (s.duration_ms for s in result.stoppages if s.duration_ms is not None),
                            Decimal(0),
                        )
                    ),
                    "excluded_shootout": len(result.excluded_shootout_event_ids),
                },
                indent=2,
            )
        )
    engine.dispose()


if __name__ == "__main__":
    main()
