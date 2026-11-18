"""Reconcile all roster players with stored participation and raw source SQL."""

import argparse
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine, text

from football_analytics.ingestion.defensive_roster import build_defensive_roster
from football_analytics.ingestion.defensive_summary import build_defensive_summary
from football_analytics.ingestion.match_participation import reconstruct_match_participation
from football_analytics.ingestion.player_scope_participation import scope_player_participation
from football_analytics.integrations.statsbomb.aerial_duels import normalize_aerial_duels
from football_analytics.integrations.statsbomb.ball_recoveries import normalize_ball_recoveries
from football_analytics.integrations.statsbomb.blocks_and_clearances import (
    normalize_blocks_and_clearances,
)
from football_analytics.integrations.statsbomb.duels import normalize_duels
from football_analytics.integrations.statsbomb.interceptions import normalize_interceptions
from football_analytics.integrations.statsbomb.lineups import normalize_lineups
from football_analytics.integrations.statsbomb.period_clock import normalize_period_clock
from football_analytics.integrations.statsbomb.pressures_and_fouls import (
    normalize_pressures_and_fouls,
)
from football_analytics.integrations.statsbomb.starting_xi import normalize_starting_xi

SQL = """
WITH raw_events AS (
 SELECT e, e#>>'{type,name}' kind FROM raw.provider_payloads p,
 LATERAL jsonb_array_elements(p.payload) e WHERE p.id = :events_id
), periods AS (
 SELECT (e->>'period')::int period,
 MAX(EXTRACT(EPOCH FROM (e->>'timestamp')::time)*1000)::bigint duration
 FROM raw_events WHERE kind='Half End' AND (e->>'period')::int <> 5 GROUP BY 1
), clock AS (
 SELECT period, COALESCE(SUM(duration) OVER (ORDER BY period ROWS BETWEEN UNBOUNDED PRECEDING
 AND 1 PRECEDING),0) start_ms, SUM(duration) OVER (ORDER BY period) end_ms FROM periods
), presence AS (
 SELECT r.id, pm.external_id player, tm.external_id team, r.has_participation_evidence,
 r.is_starter, CASE WHEN r.has_participation_evidence THEN COALESCE((
 SELECT SUM(GREATEST(0, LEAST(i.end_ms,c.end_ms)-GREATEST(i.start_ms,c.start_ms)))
 FROM analytics.player_participation_intervals i CROSS JOIN clock c
 WHERE i.participation_id=r.id AND c.period=ANY(:periods)),0) END duration
 FROM analytics.player_match_participation r JOIN core.match_lineups l ON l.id=r.lineup_id
 JOIN core.external_id_mappings pm ON pm.player_id=l.player_id AND pm.entity_type='player'
 AND pm.provider='statsbomb'
 JOIN core.external_id_mappings tm ON tm.team_id=l.team_id AND tm.entity_type='team'
 AND tm.provider='statsbomb' WHERE r.calculation_id=:calculation_id
), marked AS (
 SELECT e, kind, kind IN ('Ball Recovery','Block','Clearance','Dribbled Past','Duel',
 'Foul Committed','Foul Won','Interception','Pressure') action,
 (e#>>'{duel,type,name}'='Aerial Lost'
 OR e#>>'{pass,aerial_won}'='true' OR e#>>'{shot,aerial_won}'='true'
 OR e#>>'{clearance,aerial_won}'='true' OR e#>>'{miscontrol,aerial_won}'='true') IS TRUE aerial
 FROM raw_events WHERE (e->>'period')::int=ANY(:periods)
), stats AS (
 SELECT e#>>'{player,id}' player, e#>>'{team,id}' team, COUNT(*) n,
 COUNT(*) FILTER(WHERE action) action_n, COUNT(*) FILTER(WHERE kind='Duel') duel_n,
 COUNT(*) FILTER(WHERE aerial) aerial_n, COUNT(*) FILTER(WHERE action AND aerial) shared_n,
 COUNT(*) FILTER(WHERE aerial AND kind<>'Duel') aerial_won,
 COUNT(*) FILTER(WHERE aerial AND kind='Duel') aerial_lost,
 ARRAY_AGG(e->>'id' ORDER BY (e->>'index')::int) ids,
 ARRAY_AGG(kind ORDER BY (e->>'index')::int) FILTER(WHERE action) kinds
 FROM marked WHERE action OR aerial GROUP BY 1,2
)
SELECT p.*, COALESCE(s.n,0) n, COALESCE(s.action_n,0) action_n,
 COALESCE(s.duel_n,0) duel_n, COALESCE(s.aerial_n,0) aerial_n,
 COALESCE(s.shared_n,0) shared_n, COALESCE(s.aerial_won,0) aerial_won,
 COALESCE(s.aerial_lost,0) aerial_lost, COALESCE(s.ids,'{}') ids, COALESCE(s.kinds,'{}') kinds,
 CASE WHEN s.n>0 THEN 'observed_events' WHEN p.duration>0 THEN 'zero_with_presence'
 WHEN p.has_participation_evidence THEN 'not_applicable' ELSE 'unknown' END basis
FROM presence p LEFT JOIN stats s ON s.player=p.player AND s.team=p.team
ORDER BY p.team,p.player
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--match-id", required=True, type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    engine = create_engine(args.database_url)
    exported, checks = [], []
    with engine.connect() as connection:
        source = (
            connection.execute(
                text("""
          SELECT c.id calculation_id, c.events_payload_id, c.lineups_payload_id,
          c.events_sha256, c.lineups_sha256, e.payload events, l.payload lineups
          FROM analytics.participation_calculations c
          JOIN raw.provider_payloads e ON e.id=c.events_payload_id
          JOIN raw.provider_payloads l ON l.id=c.lineups_payload_id
          JOIN core.external_id_mappings m ON m.match_id=c.match_id
          WHERE m.provider='statsbomb' AND m.entity_type='match' AND m.external_id=:match_id
          AND c.metric_name='elapsed_on_field_ms' AND c.formula_version='1.0.0'
          AND c.events_sha256=e.sha256 AND c.lineups_sha256=l.sha256
          ORDER BY c.created_at DESC,c.id DESC LIMIT 1
        """),
                {"match_id": str(args.match_id)},
            )
            .mappings()
            .one()
        )
        events, lineups_payload = source["events"], source["lineups"]
        lineups = normalize_lineups(
            lineups_payload, args.match_id, starting_xi=normalize_starting_xi(events)
        )
        players = reconstruct_match_participation(lineups_payload, events, args.match_id)
        clock = normalize_period_clock(events)
        available = tuple(p.period for p in clock)
        actions = [
            *normalize_duels(events, match_id=args.match_id),
            *normalize_interceptions(events, match_id=args.match_id),
            *normalize_ball_recoveries(events, match_id=args.match_id),
            *normalize_blocks_and_clearances(events, match_id=args.match_id),
            *normalize_pressures_and_fouls(events, match_id=args.match_id),
        ]
        aerials = normalize_aerial_duels(events, match_id=args.match_id)
        scopes = [(p,) for p in available] + [(1, 2)]
        if len(available) == 4:
            scopes.append((3, 4))
        scopes.append(available)
        scopes = list(dict.fromkeys(scopes))
        for periods in scopes:
            summary = build_defensive_summary(
                actions,
                aerials,
                provider="statsbomb",
                match_id=str(args.match_id),
                events_sha256=source["events_sha256"],
                available_periods=available,
                periods=periods,
            )
            presence = scope_player_participation(
                players, clock, match_id=str(args.match_id), selected_periods=periods
            )
            result = build_defensive_roster(
                summary,
                lineups,
                presence,
                lineups_sha256=source["lineups_sha256"],
                participation_events_sha256=source["events_sha256"],
            )
            rows = (
                connection.execute(
                    text(SQL),
                    {
                        "events_id": source["events_payload_id"],
                        "calculation_id": source["calculation_id"],
                        "periods": list(periods),
                    },
                )
                .mappings()
                .all()
            )
            if len(rows) != len(result.players):
                raise ValueError("SQL roster population mismatch")
            for row, player in zip(rows, result.players, strict=True):
                if (
                    row["player"],
                    row["team"],
                    row["basis"],
                    row["duration"],
                    row["is_starter"],
                ) != (
                    player.lineup.external_player_id,
                    player.lineup.external_team_id,
                    player.counts_basis,
                    player.participation.elapsed_in_scope_ms,
                    player.lineup.is_starter,
                ):
                    raise ValueError("SQL player identity/participation/basis mismatch")
                actual_ids = (
                    player.observed_summary.external_event_ids if player.observed_summary else ()
                )
                if tuple(row["ids"]) != actual_ids:
                    raise ValueError("SQL player source IDs mismatch")
                if player.counts is not None:
                    counts = player.counts
                    if (
                        counts.unique_source_events,
                        counts.action_events,
                        counts.duel_events,
                        counts.aerial_results,
                        counts.shared_source_events,
                        counts.aerial_won,
                        counts.aerial_lost,
                    ) != (
                        row["n"],
                        row["action_n"],
                        row["duel_n"],
                        row["aerial_n"],
                        row["shared_n"],
                        row["aerial_won"],
                        row["aerial_lost"],
                    ):
                        raise ValueError("SQL defensive counts mismatch")
                    expected = dict.fromkeys(counts.actions_by_type, 0) | dict(
                        Counter(row["kinds"])
                    )
                    if counts.actions_by_type != expected:
                        raise ValueError("SQL action type counts mismatch")
                elif row["n"]:
                    raise ValueError("SQL events without counts")
            if sum(p.counts.unique_source_events for p in result.players if p.counts) != len(
                summary.external_event_ids
            ):
                raise ValueError("Roster totals do not recompose defensive population")
            checks.append(
                {
                    "periods": periods,
                    "players": len(rows),
                    "basis": dict(Counter(p.counts_basis for p in result.players)),
                    "source_events": len(summary.external_event_ids),
                }
            )
            exported.append(result.model_dump(mode="json"))
    engine.dispose()
    report = {
        "match_id": args.match_id,
        "calculation_id": str(source["calculation_id"]),
        "events_sha256": source["events_sha256"],
        "lineups_sha256": source["lineups_sha256"],
        "sql_verified": True,
        "checks": checks,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report | {"rosters": exported}, ensure_ascii=False, indent=2)
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
