"""Read-only PostgreSQL score-state queries versus independent local raw counters."""

import argparse
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, Engine, text

from football_analytics.analytics.executor import execute_query
from football_analytics.analytics.score_state_execution import ScoreStateQuery, ScoreStateResult
from football_analytics.infrastructure.database import get_engine

STATES = ("leading", "drawing", "trailing")
METRICS = (
    "passes.attempted",
    "shots.total",
    "shots.goals",
    "shots.xg",
    "shots.xg_available",
    "shots.missing_xg",
    "dribbles.attempted",
)


def raw_expectations(payload: list[dict], match: dict) -> tuple[tuple[str, str], dict, dict]:
    """No production normalizer, timeline or metric calculator is used here."""
    ids = tuple(str(match[f"{side}_team"][f"{side}_team_id"]) for side in ("home", "away"))
    if len(set(ids)) != 2:
        raise ValueError("Reference requires two distinct teams")
    teams = (ids[0], ids[1])
    if len({e["id"] for e in payload}) != len(payload) or len({e["index"] for e in payload}) != len(
        payload
    ):
        raise ValueError("Duplicate raw reference event")
    score = dict.fromkeys(teams, 0)
    groups: dict[tuple[str, str], list[dict]] = {(t, s): [] for t in teams for s in STATES}
    for event in sorted(payload, key=lambda e: e["index"]):
        if event["period"] == 5:
            continue
        if event["period"] not in (1, 2, 3, 4):
            raise ValueError("Invalid raw reference period")
        team = str(event.get("team", {}).get("id"))
        kind = event["type"]["name"]
        if team not in teams:
            if kind in {"Pass", "Shot", "Dribble", "Own Goal Against", "Own Goal For"}:
                raise ValueError("Reference action has missing or unknown team")
            continue
        other = next(t for t in teams if t != team)
        delta = score[team] - score[other]
        state = "leading" if delta > 0 else "trailing" if delta < 0 else "drawing"
        groups[team, state].append(event)
        if kind == "Shot" and event["shot"]["outcome"]["name"] == "Goal":
            score[team] += 1
        elif kind == "Own Goal Against":
            # Count one provider perspective; Own Goal For must not count a second time.
            score[other] += 1
    if (score[teams[0]], score[teams[1]]) != (match["home_score"], match["away_score"]):
        raise ValueError("Independent raw score differs from match record")
    expectations = {}
    for key, events in groups.items():
        kinds = Counter(e["type"]["name"] for e in events)
        shots = [e for e in events if e["type"]["name"] == "Shot"]
        xg = [
            Decimal(str(e["shot"]["statsbomb_xg"]))
            for e in shots
            if e["shot"].get("statsbomb_xg") is not None
        ]
        missing = len(shots) - len(xg)
        values = {
            "passes.attempted": kinds["Pass"],
            "shots.total": len(shots),
            "shots.goals": sum(e["shot"]["outcome"]["name"] == "Goal" for e in shots),
            "shots.xg": None if missing else sum(xg, Decimal(0)),
            "shots.xg_available": sum(xg, Decimal(0)) if xg or not shots else None,
            "shots.missing_xg": missing,
            "dribbles.attempted": kinds["Dribble"],
        }
        expected = {}
        for metric, value in values.items():
            incomplete = metric in {"shots.xg", "shots.xg_available"} and missing > 0
            eligible = (
                kinds["Pass"]
                if metric.startswith("passes.")
                else kinds["Dribble"]
                if metric.startswith("dribbles.")
                else len(shots)
            )
            covered = len(xg) if metric in {"shots.xg", "shots.xg_available"} else eligible
            expected[metric] = {
                "value": value,
                "status": "incomplete" if incomplete else "available",
                "eligible_events": eligible,
                "covered_events": covered,
            }
        expectations[key] = expected
    return teams, score, expectations


def verify_match(connection: Connection, events_path: Path, match: dict) -> dict:
    raw = events_path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    teams, score, expected = raw_expectations(json.loads(raw), match)
    checks = []
    source_fingerprints = set()
    for state in STATES:
        query = ScoreStateQuery(
            match_ids=(match["match_id"],), group_by="team", metrics=METRICS, score_state=state
        )
        result = execute_query(connection, query)
        if not isinstance(result, ScoreStateResult):
            raise ValueError("Executor did not return score-state result")
        result = ScoreStateResult.model_validate_json(result.model_dump_json())
        if result.query != query:
            raise ValueError("Returned query differs from requested score filter")
        if len(result.sources) != 1 or result.sources[0].events_sha256 != sha:
            raise ValueError("Database event source differs from the independent local file")
        source = result.sources[0]
        source_fingerprints.add(
            (
                source.events_payload_id,
                source.events_sha256,
                source.lineups_payload_id,
                source.lineups_sha256,
                source.participation_calculation_id,
            )
        )
        if (
            source.lineups_payload_id is None
            or source.lineups_sha256 is None
            or source.participation_calculation_id is None
        ):
            raise ValueError("Database provenance is incomplete")
        proof = result.score_evidence[0]
        if (
            proof.goal_status != "resolved"
            or set(proof.team_ids) != set(teams)
            or proof.final_score != tuple(score[t] for t in proof.team_ids)
        ):
            raise ValueError("Database reconstructed score differs from independent reference")
        rows = {r.team_id: r for r in result.rows}
        if len(result.rows) != 2 or set(rows) != set(teams):
            raise ValueError("Database result must contain both match teams")
        for team in teams:
            for actual in rows[team].values:
                reference = expected[team, state][actual.metric_id]
                observed = {
                    "value": actual.value,
                    "status": actual.status,
                    "eligible_events": actual.coverage.eligible_events,
                    "covered_events": actual.coverage.covered_events,
                }
                if (
                    observed != reference
                    or not actual.coverage.source_complete
                    or actual.coverage.population != "presence_confirmed"
                ):
                    raise ValueError(
                        f"Mismatch {match['match_id']} {team} {state} {actual.metric_id}: "
                        f"actual={observed!r}, expected={reference!r}"
                    )
                checks.append(
                    {
                        "team_id": team,
                        "score_state": state,
                        "metric_id": actual.metric_id,
                        **observed,
                        "review_reasons": actual.review_reasons,
                    }
                )
    if len(source_fingerprints) != 1:
        raise ValueError("Database sources changed between score-state queries")
    return {
        "match_id": match["match_id"],
        "events_sha256": sha,
        "events_payload_id": str(source.events_payload_id),
        "lineups_payload_id": str(source.lineups_payload_id),
        "lineups_sha256": source.lineups_sha256,
        "participation_calculation_id": str(source.participation_calculation_id),
        "score_by_team": score,
        "comparisons": len(checks),
        "checks": checks,
    }


def verify_batch(engine: Engine, matches: list[dict], events_dir: Path) -> dict:
    ids = [m["match_id"] for m in matches]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Select a nonempty list of distinct matches")
    verified, failures = [], []
    # One stable snapshot for the complete batch. Savepoints isolate match failures.
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            for number, match in enumerate(matches, start=1):
                try:
                    with connection.begin_nested():
                        result = verify_match(
                            connection, events_dir / f"{match['match_id']}.json", match
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
                print(
                    f"Checked {number}/{len(matches)}; "
                    f"verified={len(verified)}; failures={len(failures)}",
                    flush=True,
                )
    return {
        "verification_basis": "postgresql_executor_vs_independent_local_raw_events",
        "isolation_level": "REPEATABLE READ",
        "read_only": True,
        "requested_match_ids": ids,
        "verified": verified,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events-dir", type=Path, required=True)
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--match-id", type=int, action="append")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a new destination")
    matches_raw = args.matches.read_bytes()
    matches = json.loads(matches_raw)
    if args.match_id:
        if len(set(args.match_id)) != len(args.match_id):
            parser.error("Duplicate requested match IDs")
        matches = [m for m in matches if m["match_id"] in args.match_id]
        if {m["match_id"] for m in matches} != set(args.match_id):
            parser.error("Requested match missing from local catalogue")
    engine = get_engine()
    try:
        report = verify_batch(engine, matches, args.events_dir)
    finally:
        engine.dispose()
    report["matches_sha256"] = hashlib.sha256(matches_raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        handle.write(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
                default=lambda v: str(v) if isinstance(v, Decimal) else v,
            )
            + "\n"
        )
    print(
        json.dumps(
            {
                "verified_matches": len(report["verified"]),
                "comparisons": sum(r["comparisons"] for r in report["verified"]),
                "failures": report["failures"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if report["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
