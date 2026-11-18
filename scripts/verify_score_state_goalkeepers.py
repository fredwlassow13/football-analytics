"""Verify keeper pairs by raw pre-shot score, reusing accepted pair/role policies."""

import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import Connection, text

from football_analytics.analytics.catalogue import METRICS as CATALOGUE
from football_analytics.analytics.executor import execute_query
from football_analytics.analytics.score_state_execution import (
    ScoreStateQuery,
    ScoreStateResult,
    prepare_score_state,
    score_state_value,
)
from football_analytics.analytics.score_state_goalkeepers import goalkeeper_team_population
from football_analytics.infrastructure.database import get_engine
from football_analytics.ingestion.goalkeeper_shot_classification import classify_goalkeeper_shots
from football_analytics.integrations.statsbomb.event_relations import normalize_event_relations
from football_analytics.integrations.statsbomb.goalkeepers import normalize_goalkeeper_actions
from football_analytics.integrations.statsbomb.shots import normalize_shots

STATES = ("leading", "drawing", "trailing")
FAMILIES = frozenset({"goalkeepers"})
DEFINITIONS = {m.id: m for m in CATALOGUE if m.family in FAMILIES}
METRICS = tuple(sorted(DEFINITIONS))
SCOPES = ("counts", "penalty_counts", "non_penalty_counts", "unknown_penalty_counts")
CATEGORY_MEMBER = {
    "goal_conceded": "shot_goals_conceded",
    "on_target_save": "on_target_saves",
    "off_target_save": "off_target_saves",
    "other_shot": "other_shots",
    "review": "review_pairs",
}


def expected_scopes(
    payload: list[dict], match: dict, sha: str, population: dict
) -> tuple[tuple[str, str], dict, dict]:
    """Pair categories/roles are reused; score attribution and count assembly are separate."""
    ids = tuple(str(match[f"{side}_team"][f"{side}_team_id"]) for side in ("home", "away"))
    teams = (ids[0], ids[1])
    score = dict.fromkeys(teams, 0)
    prior = {}
    by_id = {e["id"]: e for e in payload}
    if len(by_id) != len(payload) or len({e["index"] for e in payload}) != len(payload):
        raise ValueError("Duplicate raw event")
    for event in sorted(payload, key=lambda e: e["index"]):
        if event["period"] == 5:
            continue
        prior[event["id"]] = score.copy()
        team = str(event.get("team", {}).get("id"))
        kind = event["type"]["name"]
        if kind == "Shot" and event["shot"]["outcome"]["name"] == "Goal":
            score[team] += 1
        elif kind == "Own Goal Against":
            score[next(t for t in teams if t != team)] += 1
    if tuple(score[t] for t in teams) != (match["home_score"], match["away_score"]):
        raise ValueError("Raw score differs from match record")
    classification = classify_goalkeeper_shots(
        normalize_goalkeeper_actions(payload, match_id=match["match_id"]),
        normalize_shots(payload, match_id=match["match_id"]),
        normalize_event_relations(payload, match_id=match["match_id"]),
        provider="statsbomb",
        match_id=str(match["match_id"]),
        events_sha256=sha,
    )
    unlinked = set(classification.resolution.unlinked_shot_event_ids)
    expected = {}
    for team in teams:
        other = next(t for t in teams if t != team)
        for state in STATES:

            def selected(shot_id, team=team, other=other, state=state):
                if by_id[shot_id]["period"] == 5:
                    return False
                before = prior[shot_id]
                delta = before[team] - before[other]
                observed = "leading" if delta > 0 else "trailing" if delta < 0 else "drawing"
                return observed == state

            pairs = [
                p
                for p in classification.classifications
                if str(by_id[p.external_goalkeeper_event_id]["team"]["id"]) == team
                and p.goalkeeper_period != 5
                and p.shot_period != 5
                and selected(p.external_shot_event_id)
            ]
            groups = {
                "counts": pairs,
                "penalty_counts": [p for p in pairs if p.is_penalty is True],
                "non_penalty_counts": [p for p in pairs if p.is_penalty is False],
                "unknown_penalty_counts": [p for p in pairs if p.is_penalty is None],
            }
            counts = {}
            for scope, rows in groups.items():
                tally = {member: 0 for member in CATEGORY_MEMBER.values()}
                for pair in rows:
                    tally[CATEGORY_MEMBER[pair.category]] += 1
                tally["pair_count"] = len(rows)
                counts[scope] = tally
            values = {}
            for metric, definition in DEFINITIONS.items():
                scope = dict(definition.selection)["scope"]
                known = population[team] == "presence_confirmed"
                n = counts[scope]["pair_count"] if known else 0
                values[metric] = {
                    "value": counts[scope][metric.rsplit(".", 1)[-1]] if known else None,
                    "status": "available" if known else "unknown",
                    "population": "presence_confirmed" if known else "unknown",
                    "eligible_events": n,
                    "covered_events": n,
                }
            opponent_ids = tuple(
                e["id"]
                for e in sorted(payload, key=lambda e: e["index"])
                if e["type"]["name"] == "Shot"
                and e["period"] != 5
                and str(e["team"]["id"]) != team
                and selected(e["id"])
            )
            expected[team, state] = {
                "values": values,
                "pairs": tuple(p.model_dump(mode="json") for p in pairs),
                "opponent_shots": opponent_ids,
                "unlinked_shots": tuple(key for key in opponent_ids if key in unlinked),
                "without_selected_pair": tuple(
                    key
                    for key in opponent_ids
                    if key not in {p.external_shot_event_id for p in pairs}
                ),
            }
    return teams, score, expected


def verify_match(
    events_path: Path, match: dict, lineups_path: Path, connection: Connection | None = None
) -> dict:
    raw = events_path.read_bytes()
    payload = json.loads(raw)
    sha = hashlib.sha256(raw).hexdigest()
    lineups_raw = lineups_path.read_bytes()
    lineups_sha = hashlib.sha256(lineups_raw).hexdigest()
    population = goalkeeper_team_population(
        payload,
        json.loads(lineups_raw),
        match_id=match["match_id"],
        events_sha256=sha,
        lineups_sha256=lineups_sha,
        periods=None,
    )
    teams, score, expected = expected_scopes(payload, match, sha, population)
    checks = []
    audit = []
    for state in STATES:
        query = ScoreStateQuery(
            match_ids=(match["match_id"],),
            group_by="team",
            metrics=METRICS,
            score_state=state,
            score_state_contract_version="1.3.0",
        )
        database_sources = []
        if connection is None:
            context = prepare_score_state(
                payload,
                query=query,
                match_id=match["match_id"],
                team_ids=teams,
                events_sha256=sha,
                keeper_population=population,
            )
            if context.evolution.goal_evidence.status != "resolved" or context.evolution.events[
                -1
            ].score_after != tuple(score[t] for t in context.evolution.team_ids):
                raise ValueError("Score reconstruction requires review")
            observed = {t: {m: score_state_value(m, context, t) for m in METRICS} for t in teams}
            scopes = context.goalkeeper_scopes
        else:
            result = execute_query(connection, query)
            if not isinstance(result, ScoreStateResult):
                raise ValueError("Expected score-state result")
            result = ScoreStateResult.model_validate_json(result.model_dump_json())
            if result.query != query:
                raise ValueError("Database returned a different keeper query")
            if (
                result.sources[0].events_sha256 != sha
                or result.sources[0].lineups_sha256 != lineups_sha
            ):
                raise ValueError("Database source differs from local reference")
            proof = result.score_evidence[0]
            if proof.goal_status != "resolved" or proof.final_score != tuple(
                score[t] for t in proof.team_ids
            ):
                raise ValueError("Database score differs from reference")
            observed = {r.team_id: {v.metric_id: v for v in r.values} for r in result.rows}
            scopes = {s.team_id: s for s in proof.goalkeeper_scope}
            database_sources = [s.model_dump(mode="json") for s in result.sources]
        if set(observed) != set(teams) or set(scopes) != set(teams):
            raise ValueError("Keeper result is missing a match team")
        audit.append(
            {
                "score_state": state,
                "database_sources": database_sources,
                "goalkeeper_scope": [scope.model_dump(mode="json") for scope in scopes.values()],
            }
        )
        for team in teams:
            reference = expected[team, state]
            scope = scopes[team]
            if (
                tuple(p.model_dump(mode="json") for p in scope.pairs) != reference["pairs"]
                or scope.selected_opponent_shot_event_ids != reference["opponent_shots"]
                or scope.unlinked_opponent_shot_event_ids != reference["unlinked_shots"]
                or scope.opponent_shots_without_selected_pair_ids
                != reference["without_selected_pair"]
                or scope.population != population[team]
            ):
                raise ValueError(f"Keeper pair/shot/population evidence differs: {team} {state}")
            for metric in METRICS:
                value = observed[team][metric]
                actual = {
                    "value": value.value,
                    "status": value.status,
                    "population": value.coverage.population,
                    "eligible_events": value.coverage.eligible_events,
                    "covered_events": value.coverage.covered_events,
                }
                if actual != reference["values"][metric] or not value.coverage.source_complete:
                    raise ValueError(f"Keeper mismatch {team} {state} {metric}: {actual!r}")
                checks.append(
                    {
                        "team_id": team,
                        "score_state": state,
                        "metric_id": metric,
                        **actual,
                        "review_reasons": value.review_reasons,
                    }
                )

    return {
        "match_id": match["match_id"],
        "events_sha256": sha,
        "lineups_sha256": lineups_sha,
        "comparisons": len(checks),
        "checks": checks,
        "goalkeeper_evidence": audit,
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
            "accepted_pair_classifier_and_role_policy_plus_raw_defender_pre_shot_score"
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
