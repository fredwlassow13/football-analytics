"""Independent SQL checks for structured networks and defensive maps."""

from collections import Counter
from collections.abc import Sequence
from math import isclose
from typing import Literal

from sqlalchemy import Connection, text

from football_analytics.analytics.contracts import Contract, SourceReference
from football_analytics.analytics.sequence_verification import (
    verify_sequence_result,
)
from football_analytics.analytics.structured_contracts import (
    AttackData,
    MapData,
    MapOptions,
    NetworkData,
    PossessionData,
    StructuredQuery,
    StructuredResult,
)
from football_analytics.analytics.structured_executor import execute_structured_query

NETWORK_SQL = """
WITH passes AS (
    SELECT e->>'id' id, (e->>'index')::int idx,
           e#>>'{player,id}' sender, e#>>'{pass,recipient,id}' recipient,
           e#>>'{pass,outcome,name}' outcome,
           (e#>>'{location,0}')::double precision x,
           (e#>>'{location,1}')::double precision y
    FROM raw.provider_payloads p, LATERAL jsonb_array_elements(p.payload) e
    WHERE p.id=:payload_id AND e#>>'{type,name}'='Pass'
      AND (e->>'period')::int=ANY(CAST(:periods AS int[]))
      AND e#>>'{team,id}'=:team
)
SELECT 'node' kind,sender,NULL::text recipient,array_agg(id ORDER BY idx) ids,
       avg(x) x,avg(y) y,
       bool_or(coalesce(outcome='Unknown',false)) unknown_outcome,
       bool_or(x<0 OR x>120 OR y<0 OR y>80) outside
FROM passes GROUP BY sender
UNION ALL
SELECT 'edge',sender,recipient,array_agg(id ORDER BY idx),NULL::double precision,
       NULL::double precision,false,false
FROM passes WHERE outcome IS NULL GROUP BY sender,recipient
"""

_AERIAL = """(
    (kind='Duel' AND e#>>'{duel,type,name}'='Aerial Lost') OR
    (kind='Pass' AND e#>>'{pass,aerial_won}'='true') OR
    (kind='Clearance' AND e#>>'{clearance,aerial_won}'='true') OR
    (kind='Shot' AND e#>>'{shot,aerial_won}'='true') OR
    (kind='Miscontrol' AND e#>>'{miscontrol,aerial_won}'='true')
)"""
_GROUPS = {
    "all": "kind IN ('Ball Recovery','Block','Clearance','Dribbled Past','Duel',"
    "'Foul Committed','Foul Won','Interception','Pressure') OR " + _AERIAL,
    "recoveries": "kind IN ('Ball Recovery','Interception')",
    "duels": "kind='Duel'",
    "pressure": "kind IN ('Pressure','Dribbled Past')",
    "blocks_clearances": "kind IN ('Block','Clearance')",
    "fouls": "kind IN ('Foul Committed','Foul Won')",
    "aerials": _AERIAL,
}
MAP_SQL = """
WITH events AS (
    SELECT e,e#>>'{type,name}' kind,(e#>>'{location,0}')::double precision x,
           (e#>>'{location,1}')::double precision y
    FROM raw.provider_payloads p,LATERAL jsonb_array_elements(p.payload) e
    WHERE p.id=:payload_id AND (e->>'period')::int=ANY(CAST(:periods AS int[]))
      AND e#>>'{team,id}'=:team
      AND (CAST(:player AS text) IS NULL OR e#>>'{player,id}'=:player)
)
SELECT e->>'id' id,kind,x,y,
       CASE WHEN x IS NULL OR y IS NULL THEN 'missing_location'
            WHEN x<0 OR x>120 OR y<0 OR y>80 THEN 'outside_pitch' END reason,
       least(floor(x/120*CAST(:columns AS int))::int,CAST(:columns AS int)-1) column_index,
       least(floor(y/80*CAST(:rows AS int))::int,CAST(:rows AS int)-1) row_index
FROM events WHERE ({condition}) ORDER BY (e->>'index')::int
"""


class StructuredCheck(Contract):
    query: StructuredQuery
    status: Literal["validated", "validated_review", "unavailable", "failed"]
    comparisons: int = 0
    source: SourceReference | None = None
    reasons: tuple[str, ...] = ()
    error: str | None = None


def _same(actual: object, expected: object, label: str) -> int:
    if actual != expected:
        raise ValueError(f"SQL mismatch: {label}")
    return 1


def verify_structured_result(connection: Connection, result: StructuredResult) -> StructuredCheck:
    result = StructuredResult.model_validate(result.model_dump())
    if result.status == "unavailable":
        return StructuredCheck(
            query=result.query, status="unavailable", source=result.source, reasons=result.reasons
        )
    source = result.source
    if source is None:
        raise ValueError("Result has no source")
    meta = (
        connection.execute(
            text("""
        SELECT provider,endpoint,sha256 FROM raw.provider_payloads WHERE id=:id
    """),
            {"id": source.events_payload_id},
        )
        .mappings()
        .one()
    )
    checks = _same(
        (meta["provider"], meta["sha256"]),
        (result.query.provider, source.events_sha256),
        "source metadata",
    )
    if not meta["endpoint"].endswith(f"/events/{result.query.match_id}.json"):
        raise ValueError("SQL source belongs to another match")
    params = {
        "payload_id": source.events_payload_id,
        "periods": list(result.query.periods or source.available_periods),
        "team": result.query.team_id,
    }
    reasons: set[str] = set()
    if isinstance(result.data, NetworkData):
        rows = connection.execute(text(NETWORK_SQL), params).mappings().all()
        expected_nodes = {r["sender"]: r for r in rows if r["kind"] == "node"}
        expected_edges = {
            (r["sender"], r["recipient"]): tuple(r["ids"]) for r in rows if r["kind"] == "edge"
        }
        checks += _same(
            {n.external_player_id for n in result.data.nodes}, set(expected_nodes), "network nodes"
        )
        checks += _same(
            {
                (e.external_sender_player_id, e.external_recipient_player_id): e.external_event_ids
                for e in result.data.connections
            },
            expected_edges,
            "network connections",
        )
        for node in result.data.nodes:
            raw = expected_nodes[node.external_player_id]
            checks += _same(node.external_event_ids, tuple(raw["ids"]), "node origins")
            if not isclose(node.mean_x, float(raw["x"]), rel_tol=0, abs_tol=1e-9) or not isclose(
                node.mean_y, float(raw["y"]), rel_tol=0, abs_tol=1e-9
            ):
                raise ValueError("SQL mismatch: network mean origin")
            checks += 1
        if any(target not in expected_nodes for _, target in expected_edges):
            reasons.add("recipient_without_origin")
        if any(r["unknown_outcome"] for r in expected_nodes.values()):
            reasons.add("network_pass_outcome_unknown")
        if any(r["outside"] for r in expected_nodes.values()):
            reasons.add("network_origin_outside_pitch")
    elif isinstance(result.data, MapData):
        value = result.data.value
        params.update(player=result.query.player_id, columns=value.columns, rows=value.rows)
        rows = (
            connection.execute(text(MAP_SQL.replace("{condition}", _GROUPS[value.group])), params)
            .mappings()
            .all()
        )
        checks += _same(value.candidate_event_ids, tuple(r["id"] for r in rows), "map candidates")
        checks += _same(
            value.candidate_counts_by_type,
            dict(Counter(r["kind"] for r in rows)),
            "map type counts",
        )
        located = [r for r in rows if r["reason"] is None]
        checks += _same(
            tuple(p.external_event_id for p in value.points),
            tuple(r["id"] for r in located),
            "map points",
        )
        for point, raw in zip(value.points, located, strict=True):
            if point.start_location is None:
                raise ValueError("Map point has no location")
            checks += _same(
                (point.start_location.x, point.start_location.y),
                (raw["x"], raw["y"]),
                "map source coordinates",
            )
        checks += _same(
            {i.external_event_id: i.reason for i in value.location_issues},
            {r["id"]: r["reason"] for r in rows if r["reason"] is not None},
            "map location issues",
        )
        cells: dict[tuple[int, int], list[str]] = {
            (row, column): [] for row in range(value.rows) for column in range(value.columns)
        }
        for raw in located:
            cells[raw["row_index"], raw["column_index"]].append(raw["id"])
        actual = {(c.row, c.column): c.external_event_ids for c in value.cells}
        checks += _same(len(value.cells), value.rows * value.columns, "grid size")
        checks += _same(actual, {k: tuple(v) for k, v in cells.items()}, "grid event membership")
        if any(r["reason"] is not None for r in rows):
            reasons.add("map_location_issues")
    elif isinstance(result.data, (PossessionData, AttackData)):
        sequence_checks, reasons = verify_sequence_result(
            connection,
            result,
        )
        checks += sequence_checks

    else:
        raise ValueError("SQL verification is not implemented for this resource")
    checks += _same(set(result.reasons), reasons, "review reasons")
    checks += _same(result.status, "review" if reasons else "available", "result status")
    return StructuredCheck(
        query=result.query,
        status="validated_review" if reasons else "validated",
        comparisons=checks,
        source=source,
        reasons=result.reasons,
    )


def verify_structured_queries(
    connection: Connection,
    queries: Sequence[StructuredQuery],
) -> tuple[StructuredCheck, ...]:
    if not queries:
        raise ValueError("Provide at least one query")
    if len({q.model_dump_json() for q in queries}) != len(queries):
        raise ValueError("Duplicate structured queries")
    items = []
    for query in queries:
        result = None
        try:
            with connection.begin_nested():
                result = execute_structured_query(connection, query)
                check = verify_structured_result(connection, result)
        except Exception as exc:
            check = StructuredCheck(
                query=query,
                status="failed",
                source=result.source if result else None,
                error=f"{type(exc).__name__}: {exc}",
            )
        items.append(check)
    return tuple(items)


def main() -> None:
    import argparse
    import json
    from pathlib import Path

    from sqlalchemy import create_engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--competition-id", required=True, type=int)
    parser.add_argument("--season-id", required=True, type=int)
    parser.add_argument("--match-ids", nargs="+", type=int)
    parser.add_argument("--expected-matches", required=True, type=int)
    parser.add_argument("--map-groups", nargs="+", choices=tuple(_GROUPS), default=["all"])
    parser.add_argument(
        "--sequences-only",
        action="store_true",
        help="Verify fragments and attacks, without networks/maps",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if min(args.competition_id, args.season_id, args.expected_matches) <= 0:
        parser.error("Competition, season and expected count must be positive")
    if len(args.map_groups) != len(set(args.map_groups)):
        parser.error("Duplicate map groups")
    if args.match_ids is not None and (
        any(mid <= 0 for mid in args.match_ids) or len(args.match_ids) != len(set(args.match_ids))
    ):
        parser.error("Match IDs must be unique and positive")
    engine = create_engine(args.database_url)
    items: list[StructuredCheck] = []
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                matches = (
                    connection.execute(
                        text("""
                    SELECT mm.external_id mid,hm.external_id home,am.external_id away
                    FROM core.matches m JOIN core.seasons s ON s.id=m.season_id
                    JOIN core.external_id_mappings mm ON mm.match_id=m.id
                        AND mm.provider='statsbomb' AND mm.entity_type='match'
                    JOIN core.external_id_mappings hm ON hm.team_id=m.home_team_id
                        AND hm.provider='statsbomb' AND hm.entity_type='team'
                    JOIN core.external_id_mappings am ON am.team_id=m.away_team_id
                        AND am.provider='statsbomb' AND am.entity_type='team'
                    JOIN core.external_id_mappings cm ON cm.competition_id=s.competition_id
                        AND cm.provider='statsbomb' AND cm.entity_type='competition'
                    JOIN core.external_id_mappings sm ON sm.season_id=s.id
                        AND sm.provider='statsbomb' AND sm.entity_type='season'
                    WHERE cm.external_id=:competition AND sm.external_id=:season
                    ORDER BY m.match_date,mm.external_id
                """),
                        {
                            "competition": str(args.competition_id),
                            "season": f"{args.competition_id}:{args.season_id}",
                        },
                    )
                    .mappings()
                    .all()
                )
                if args.match_ids is not None:
                    found = {int(m["mid"]) for m in matches}
                    if not set(args.match_ids).issubset(found):
                        raise ValueError("Requested match is outside stored competition/season")
                    matches = [m for m in matches if int(m["mid"]) in args.match_ids]
                ids = [int(m["mid"]) for m in matches]
                if len(ids) != len(set(ids)) or len(ids) != args.expected_matches:
                    raise ValueError("Stored match selection differs from expected count")
                for number, match in enumerate(matches, 1):
                    if match["home"] == match["away"]:
                        raise ValueError("Match has identical home/away mapping")
                    queries = []
                    if args.sequences_only:
                        queries = [
                            StructuredQuery(
                                resource_id="analysis.possession_fragments",
                                match_id=int(match["mid"]),
                            ),
                            StructuredQuery(
                                resource_id="analysis.attacks",
                                match_id=int(match["mid"]),
                            ),
                        ]

                    else:
                        for team in (match["home"], match["away"]):
                            common = dict(
                                match_id=int(match["mid"]),
                                team_id=team,
                            )

                            queries.append(
                                StructuredQuery(
                                    resource_id="analysis.pass_connections",
                                    **common,
                                )
                            )

                            for group in args.map_groups:
                                queries.append(
                                    StructuredQuery(
                                        resource_id="analysis.defensive_map",
                                        map_options=MapOptions.model_validate({"group": group}),
                                        **common,
                                    )
                                )
                    items.extend(verify_structured_queries(connection, queries))
                    print(f"Processed {number}/{len(matches)} matches", flush=True)
    finally:
        engine.dispose()
    counts = Counter(item.status for item in items)
    summary = {
        "verification_scope": (
            "sql_source_membership_and_deterministic_replay"
            if args.sequences_only
            else "sql_networks_and_maps"
        ),
        "matches": len(ids),
        "queries": len(items),
        "statuses": {
            "validated": counts["validated"],
            "validated_review": counts["validated_review"],
            "unavailable": counts["unavailable"],
            "failed": counts["failed"],
        },
        "comparisons": sum(item.comparisons for item in items),
        "output": str(args.output),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            summary | {"items": [item.model_dump(mode="json") for item in items]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if counts["failed"]:
        raise SystemExit(1)
    if counts["unavailable"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
