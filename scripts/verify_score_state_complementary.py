"""Verify progression/control counts against existing classifiers and raw pre-action scores."""

import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import Connection, text

from football_analytics.analytics import complementary_execution as adapters
from football_analytics.analytics.catalogue import METRICS as CATALOGUE
from football_analytics.analytics.executor import execute_query
from football_analytics.analytics.score_state_execution import (
    ScoreStateQuery,
    ScoreStateResult,
    prepare_score_state,
    score_state_value,
)
from football_analytics.infrastructure.database import get_engine
from football_analytics.ingestion.ball_control_evidence import classify_ball_control_evidence
from football_analytics.ingestion.movement_progression import classify_movement_progression

STATES = ("leading", "drawing", "trailing")
FAMILIES = frozenset({"movement", "ball_control"})
DEFINITIONS = {m.id: m for m in CATALOGUE if m.family in FAMILIES}
METRICS = tuple(sorted(DEFINITIONS))


def reference(payload, match):
    teams = tuple(str(match[f"{s}_team"][f"{s}_team_id"]) for s in ("home", "away"))
    score = dict.fromkeys(teams, 0)
    prior = {}
    if len({e["id"] for e in payload}) != len(payload) or len({e["index"] for e in payload}) != len(
        payload
    ):
        raise ValueError("Duplicate raw event")
    for e in sorted(payload, key=lambda e: e["index"]):
        if e["period"] == 5:
            continue
        t = str(e.get("team", {}).get("id"))
        if t in teams:
            other = next(x for x in teams if x != t)
            prior[e["id"]] = (
                "leading"
                if score[t] > score[other]
                else "trailing"
                if score[t] < score[other]
                else "drawing"
            )
        if e["type"]["name"] == "Shot" and e["shot"]["outcome"]["name"] == "Goal":
            score[t] += 1
        elif e["type"]["name"] == "Own Goal Against":
            score[next(x for x in teams if x != t)] += 1
    if tuple(score[t] for t in teams) != (match["home_score"], match["away_score"]):
        raise ValueError("Raw score differs from match catalogue")
    mid = match["match_id"]
    spatial = {
        s.external_event_id: s for s in adapters.normalize_event_spatial(payload, match_id=mid)
    }
    passes = adapters.normalize_passes(payload, match_id=mid)
    pass_by_id = {p.external_event_id: p for p in passes}
    movements = [
        classify_movement_progression(s, pass_event=pass_by_id.get(s.external_event_id))
        for s in spatial.values()
        if s.event_type in {"Pass", "Carry"} and s.period != 5
    ]
    actions = [
        *passes,
        *adapters.normalize_ball_control_incidents(payload, match_id=mid),
        *adapters.normalize_dribbles(payload, match_id=mid),
        *adapters.normalize_ball_recoveries(payload, match_id=mid),
        *adapters.normalize_interceptions(payload, match_id=mid),
        *adapters.normalize_duels(payload, match_id=mid),
        *adapters.normalize_blocks_and_clearances(payload, match_id=mid),
        *(
            a
            for a in adapters.normalize_pressures_and_fouls(payload, match_id=mid)
            if isinstance(a, adapters.MatchPressure | adapters.MatchDribbledPast)
        ),
    ]
    controls = [
        classify_ball_control_evidence(a, spatial[a.external_event_id])
        for a in actions
        if a.period != 5
    ]
    expected = {}
    scopes = {}
    for t in teams:
        for state in STATES:
            scoped_m = [
                m
                for m in movements
                if m.geometry.source.external_team_id == t
                and prior[m.geometry.source.external_event_id] == state
            ]
            scoped_c = [
                c
                for c in controls
                if c.source.external_team_id == t and prior[c.source.external_event_id] == state
            ]
            for family, items in (
                ("movement", [m.geometry.source for m in scoped_m]),
                ("ball_control", [c.source for c in scoped_c]),
            ):
                scopes[t, state, family] = tuple(
                    e.external_event_id for e in sorted(items, key=lambda e: e.source_index)
                )
            for name, d in DEFINITIONS.items():
                selection = dict(d.selection)
                if d.family == "movement":
                    rows = [
                        m
                        for m in scoped_m
                        if m.geometry.source.event_type == selection["event_type"]
                    ]
                    wanted = {"true": True, "false": False, "unknown": None}[
                        name.rsplit(".", 1)[-1]
                    ]
                    count = sum(getattr(m, selection["metric"]) is wanted for m in rows)
                    n = len(rows)
                else:
                    count = sum(c.category == selection["category"] for c in scoped_c)
                    n = len(scoped_c)
                expected[t, state, name] = (count, n)
    return teams, score, expected, scopes


def verify_match(
    events_path: Path, match: dict, lineups_path: Path, connection: Connection | None = None
) -> dict:
    raw = events_path.read_bytes()
    payload = json.loads(raw)
    sha = hashlib.sha256(raw).hexdigest()
    lineups_sha = hashlib.sha256(lineups_path.read_bytes()).hexdigest()
    teams, score, expected, scopes = reference(payload, match)
    checks = []
    audit = []
    for state in STATES:
        query = ScoreStateQuery(
            match_ids=(match["match_id"],),
            group_by="team",
            metrics=METRICS,
            score_state=state,
            score_state_contract_version="1.4.0",
        )
        sources = []
        if connection is None:
            context = prepare_score_state(
                payload, query=query, match_id=match["match_id"], team_ids=teams, events_sha256=sha
            )
            if context.evolution.goal_evidence.status != "resolved":
                raise ValueError("Score reconstruction requires review")
            if context.evolution.events[-1].score_after != tuple(score[t] for t in teams):
                raise ValueError("Reconstructed score differs")
            observed = {t: {m: score_state_value(m, context, t) for m in METRICS} for t in teams}
            evidence = context.complementary_scopes
        else:
            result = execute_query(connection, query)
            if not isinstance(result, ScoreStateResult):
                raise ValueError("Expected score-state result")
            result = ScoreStateResult.model_validate_json(result.model_dump_json())
            if result.query != query or len(result.sources) != 1 or len(result.score_evidence) != 1:
                raise ValueError("Database query/source scope differs")
            if (
                result.sources[0].events_sha256 != sha
                or result.sources[0].lineups_sha256 != lineups_sha
            ):
                raise ValueError("Database source differs from local reference")
            proof = result.score_evidence[0]
            if proof.goal_status != "resolved" or proof.final_score != tuple(
                score[t] for t in proof.team_ids
            ):
                raise ValueError("Database score differs")
            observed = {row.team_id: {v.metric_id: v for v in row.values} for row in result.rows}
            evidence = proof.complementary_scope
            sources = [s.model_dump(mode="json") for s in result.sources]
        if set(observed) != set(teams):
            raise ValueError("Missing match team")
        for scope in evidence:
            if scope.selected_event_ids != scopes[scope.team_id, state, scope.family]:
                raise ValueError("Selected event evidence differs")
        if {(s.team_id, s.family) for s in evidence} != {(t, f) for t in teams for f in FAMILIES}:
            raise ValueError("Missing family evidence")
        audit.append(
            {
                "score_state": state,
                "sources": sources,
                "scope": [s.model_dump(mode="json") for s in evidence],
            }
        )
        for t in teams:
            for m in METRICS:
                v = observed[t][m]
                count, n = expected[t, state, m]
                if (
                    v.status != "available"
                    or v.value != count
                    or not v.coverage.source_complete
                    or v.coverage.population != "presence_confirmed"
                    or v.coverage.eligible_events != n
                    or v.coverage.covered_events != n
                ):
                    raise ValueError(f"Count/coverage mismatch: {t} {state} {m}")
                checks.append(
                    {
                        "team_id": t,
                        "score_state": state,
                        "metric_id": m,
                        "value": v.value,
                        "status": v.status,
                        "eligible_events": n,
                        "review_reasons": v.review_reasons,
                    }
                )
    return {
        "match_id": match["match_id"],
        "events_sha256": sha,
        "lineups_sha256": lineups_sha,
        "comparisons": len(checks),
        "checks": checks,
        "complementary_evidence": audit,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events-dir", type=Path, required=True)
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--lineups-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--database", action="store_true", help="Use configured PostgreSQL read-only"
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a new destination")
    matches_raw = args.matches.read_bytes()
    matches = json.loads(matches_raw)
    if not matches or len({m["match_id"] for m in matches}) != len(matches):
        parser.error("Nonempty distinct match catalogue required")
    verified, failures = [], []

    def check(match: dict, connection: Connection | None = None) -> None:
        try:
            verified.append(
                verify_match(
                    args.events_dir / f"{match['match_id']}.json",
                    match,
                    args.lineups_dir / f"{match['match_id']}.json",
                    connection,
                )
            )
        except Exception as exc:
            failures.append(
                {"match_id": match["match_id"], "error_type": type(exc).__name__, "error": str(exc)}
            )
        print(f"Checked {len(verified) + len(failures)}/{len(matches)}", flush=True)

    if args.database:
        engine = get_engine()
        try:
            with engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection:
                with connection.begin():
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                    for match in matches:
                        # Exception must leave the savepoint before continuing the batch.
                        try:
                            with connection.begin_nested():
                                result = verify_match(
                                    args.events_dir / f"{match['match_id']}.json",
                                    match,
                                    args.lineups_dir / f"{match['match_id']}.json",
                                    connection,
                                )
                            verified.append(result)
                        except Exception as exc:
                            failures.append(
                                {
                                    "match_id": match["match_id"],
                                    "error_type": type(exc).__name__,
                                    "error": str(exc),
                                }
                            )
                        print(f"Checked {len(verified) + len(failures)}/{len(matches)}", flush=True)
        finally:
            engine.dispose()
    else:
        for match in matches:
            check(match)
    report = {
        "execution": "postgresql_read_only" if args.database else "local",
        "reference_basis": (
            "accepted_event_classifiers_plus_independent_raw_pre_action_score_and_counts"
        ),
        "matches_sha256": hashlib.sha256(matches_raw).hexdigest(),
        "verified": verified,
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
    print(
        json.dumps(
            {
                "verified_matches": len(verified),
                "comparisons": sum(v["comparisons"] for v in verified),
                "failures": failures,
                "execution": report["execution"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
