"""Export defensive maps from a stored payload and reconcile each grid with SQL."""

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine, text

from football_analytics.ingestion.defensive_map import DEFENSIVE_MAP_GROUPS, build_defensive_map
from football_analytics.integrations.statsbomb.aerial_duels import normalize_aerial_duels
from football_analytics.integrations.statsbomb.ball_recoveries import normalize_ball_recoveries
from football_analytics.integrations.statsbomb.blocks_and_clearances import (
    normalize_blocks_and_clearances,
)
from football_analytics.integrations.statsbomb.duels import normalize_duels
from football_analytics.integrations.statsbomb.interceptions import normalize_interceptions
from football_analytics.integrations.statsbomb.period_clock import normalize_period_clock
from football_analytics.integrations.statsbomb.pressures_and_fouls import (
    normalize_pressures_and_fouls,
)
from football_analytics.integrations.statsbomb.spatial import normalize_event_spatial
from football_analytics.visualization.defensive_map import render_defensive_maps

SQL = """
WITH source AS (
 SELECT e, e#>>'{type,name}' AS kind,
 (e#>>'{location,0}')::numeric AS x, (e#>>'{location,1}')::numeric AS y,
 (e#>>'{duel,type,name}' = 'Aerial Lost'
  OR e#>>'{pass,aerial_won}' = 'true' OR e#>>'{shot,aerial_won}' = 'true'
  OR e#>>'{clearance,aerial_won}' = 'true'
  OR e#>>'{miscontrol,aerial_won}' = 'true') IS TRUE AS aerial
 FROM raw.provider_payloads p, LATERAL jsonb_array_elements(p.payload) e WHERE p.id = :id
), selected AS (
 SELECT * FROM source WHERE
 (kind IN ('Ball Recovery','Block','Clearance','Dribbled Past','Duel','Foul Committed',
           'Foul Won','Interception','Pressure') OR aerial)
 AND e#>>'{team,id}' = :team AND (e->>'period')::int = ANY(:periods)
 AND (CAST(:player AS text) IS NULL OR e#>>'{player,id}' = :player)
 AND (:group_name = 'all' OR (:group_name = 'aerials' AND aerial) OR kind = ANY(:group_types))
 AND (:all_types OR kind = ANY(:event_types))
)
SELECT e->>'id' AS id,
 CASE WHEN x BETWEEN 0 AND 120 AND y BETWEEN 0 AND 80
      THEN LEAST(FLOOR(x * :columns / 120)::int, :columns - 1) END AS col,
 CASE WHEN x BETWEEN 0 AND 120 AND y BETWEEN 0 AND 80
      THEN LEAST(FLOOR(y * :rows / 80)::int, :rows - 1) END AS row
FROM selected ORDER BY (e->>'index')::int
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--match-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Output stem without extension")
    parser.add_argument("--group", default="all", choices=["all", "aerials", *DEFENSIVE_MAP_GROUPS])
    parser.add_argument("--event-types", nargs="+")
    parser.add_argument("--periods", nargs="+", type=int)
    parser.add_argument("--team-id")
    parser.add_argument("--player-id")
    parser.add_argument("--compare-periods", action="store_true")
    parser.add_argument("--mode", choices=["count", "percentage"], default="count")
    parser.add_argument("--columns", type=int, default=12)
    parser.add_argument("--rows", type=int, default=8)
    args = parser.parse_args()
    engine = create_engine(args.database_url)
    with engine.connect() as connection:
        source = (
            connection.execute(
                text("""
          SELECT id,payload,sha256 FROM raw.provider_payloads WHERE provider='statsbomb'
          AND endpoint LIKE :endpoint ORDER BY fetched_at DESC, id DESC LIMIT 1
        """),
                {"endpoint": f"%/events/{args.match_id}.json"},
            )
            .mappings()
            .one()
        )
        payload = source["payload"]
        clock = normalize_period_clock(payload)
        available = tuple(p.period for p in clock)
        actions = [
            *normalize_duels(payload, match_id=args.match_id),
            *normalize_interceptions(payload, match_id=args.match_id),
            *normalize_ball_recoveries(payload, match_id=args.match_id),
            *normalize_blocks_and_clearances(payload, match_id=args.match_id),
            *normalize_pressures_and_fouls(payload, match_id=args.match_id),
        ]
        aerials = normalize_aerial_duels(payload, match_id=args.match_id)
        spatial = normalize_event_spatial(payload, match_id=args.match_id)
        names = {str(e["team"]["id"]): e["team"]["name"] for e in payload if "team" in e}
        teams = [args.team_id] if args.team_id else sorted(names)
        if args.player_id and not args.team_id:
            raise ValueError("Player selection requires --team-id")
        requested = tuple(args.periods) if args.periods else available
        scopes = [(p,) for p in requested] if args.compare_periods else [requested]
        if len(teams) * len(scopes) > 4:
            raise ValueError("Select at most four team/period panels per output")
        maps, labels = [], []
        for periods in scopes:
            for team in teams:
                result = build_defensive_map(
                    actions,
                    aerials,
                    spatial,
                    provider="statsbomb",
                    match_id=str(args.match_id),
                    events_sha256=source["sha256"],
                    team_id=team,
                    available_periods=available,
                    periods=periods,
                    player_id=args.player_id,
                    group=args.group,
                    event_types=tuple(args.event_types) if args.event_types else None,
                    columns=args.columns,
                    rows=args.rows,
                )
                records = (
                    connection.execute(
                        text(SQL),
                        {
                            "id": source["id"],
                            "team": team,
                            "periods": list(periods),
                            "player": args.player_id,
                            "group_name": args.group,
                            "group_types": list(DEFENSIVE_MAP_GROUPS.get(args.group, ())),
                            "all_types": args.event_types is None,
                            "event_types": args.event_types or [],
                            "columns": args.columns,
                            "rows": args.rows,
                        },
                    )
                    .mappings()
                    .all()
                )
                if tuple(r["id"] for r in records) != result.candidate_event_ids:
                    raise ValueError("SQL candidate selection mismatch")
                expected = {r["id"]: (r["row"], r["col"]) for r in records if r["row"] is not None}
                actual = {
                    key: (c.row, c.column) for c in result.cells for key in c.external_event_ids
                }
                if expected != actual:
                    raise ValueError("SQL grid mismatch")
                maps.append(result)
                label = names[team]
                if args.player_id:
                    label += " · " + next(
                        (
                            e["player"]["name"]
                            for e in payload
                            if str(e.get("player", {}).get("id")) == args.player_id
                        ),
                        args.player_id,
                    )
                labels.append(label)
    engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".png", ".svg"):
        render_defensive_maps(
            maps,
            labels=labels,
            output_path=args.output.with_suffix(suffix),
            title=f"Mapas defensivos · partida {args.match_id}",
            mode=args.mode,
        )
    args.output.with_suffix(".json").write_text(
        json.dumps(
            {
                "sql_verified": True,
                "mode": args.mode,
                "maps": [m.model_dump(mode="json") for m in maps],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sql_verified": True,
                "panels": [
                    {
                        "label": label,
                        "periods": m.periods,
                        "selected": m.candidate_count,
                        "located": m.located_count,
                        "issues": len(m.location_issues),
                    }
                    for label, m in zip(labels, maps, strict=True)
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
