"""Verify creation attribution by raw pre-pass score; optionally query PostgreSQL."""

import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import Connection, text

from football_analytics.analytics.executor import execute_query
from football_analytics.analytics.score_state_execution import (
    ScoreStateQuery,
    ScoreStateResult,
    prepare_score_state,
    score_state_value,
)
from football_analytics.infrastructure.database import get_engine
from football_analytics.ingestion.creation_classification import classify_creation_links
from football_analytics.integrations.statsbomb.creation import normalize_creation_events
from football_analytics.integrations.statsbomb.events import normalize_events

STATES = ("leading", "drawing", "trailing")
MEMBERS = (
    "pass_count",
    "candidate_pairs",
    "usable_pairs",
    "review_pairs",
    "recorded_shot_assists",
    "recorded_goal_assists",
    "passes_with_any_recorded_flag",
    "passes_with_both_recorded_flags",
    "usable_pairs_without_positive_flag",
)
METRICS = tuple("creation." + name for name in MEMBERS)


def expected_scopes(
    payload: list[dict], match: dict, sha: str
) -> tuple[tuple[str, str], dict, dict]:
    """Reuse the accepted classifier; independently assign raw passes to score states."""
    ids = tuple(str(match[f"{side}_team"][f"{side}_team_id"]) for side in ("home", "away"))
    teams = (ids[0], ids[1])
    if len(set(teams)) != 2:
        raise ValueError("Two reference teams required")
    score = dict.fromkeys(teams, 0)
    passes: dict[tuple[str, str], list[dict]] = {(t, s): [] for t in teams for s in STATES}
    for event in sorted(payload, key=lambda e: e["index"]):
        if event["period"] == 5:
            continue
        team = str(event.get("team", {}).get("id"))
        if team not in teams:
            continue
        other = next(t for t in teams if t != team)
        delta = score[team] - score[other]
        state = "leading" if delta > 0 else "trailing" if delta < 0 else "drawing"
        kind = event["type"]["name"]
        if kind == "Pass":
            passes[team, state].append(event)
        elif kind == "Shot" and event["shot"]["outcome"]["name"] == "Goal":
            score[team] += 1
        elif kind == "Own Goal Against":
            score[other] += 1
    if tuple(score[t] for t in teams) != (match["home_score"], match["away_score"]):
        raise ValueError("Raw score differs from match record")
    classification = classify_creation_links(
        normalize_creation_events(payload, match_id=match["match_id"]),
        normalize_events(payload, match_id=match["match_id"]),
        provider="statsbomb",
        match_id=str(match["match_id"]),
        events_sha256=sha,
    )
    expected = {}
    for key, raw_passes in passes.items():
        selected = {p["id"]: p for p in raw_passes}
        pairs = [p for p in classification.classifications if p.external_pass_event_id in selected]
        usable = [p for p in pairs if p.status == "usable"]
        usable_passes = {p.external_pass_event_id for p in usable}
        shot_flags = {
            key for key in usable_passes if selected[key]["pass"].get("shot_assist") is True
        }
        goal_flags = {
            key for key in usable_passes if selected[key]["pass"].get("goal_assist") is True
        }
        expected[key] = {
            "values": dict(
                zip(
                    METRICS,
                    (
                        len(selected),
                        len(pairs),
                        len(usable),
                        len(pairs) - len(usable),
                        len(shot_flags),
                        len(goal_flags),
                        len(shot_flags | goal_flags),
                        len(shot_flags & goal_flags),
                        sum(
                            p.external_pass_event_id not in shot_flags | goal_flags for p in usable
                        ),
                    ),
                    strict=True,
                )
            ),
            "pass_event_ids": tuple(p["id"] for p in raw_passes),
            "pairs": tuple(p.model_dump(mode="json") for p in pairs),
        }
    return teams, score, expected


def verify_match(events_path: Path, match: dict, connection: Connection | None = None) -> dict:
    raw = events_path.read_bytes()
    payload = json.loads(raw)
    sha = hashlib.sha256(raw).hexdigest()
    teams, score, expected = expected_scopes(payload, match, sha)
    checks = []
    audit = []
    for state in STATES:
        query = ScoreStateQuery(
            match_ids=(match["match_id"],),
            group_by="team",
            metrics=METRICS,
            score_state=state,
            score_state_contract_version="1.1.0",
        )
        database_sources = []
        if connection is None:
            context = prepare_score_state(
                payload, query=query, match_id=match["match_id"], team_ids=teams, events_sha256=sha
            )
            if context.evolution.goal_evidence.status != "resolved" or context.evolution.events[
                -1
            ].score_after != tuple(score[t] for t in context.evolution.team_ids):
                raise ValueError("Score reconstruction requires review")
            observed = {t: {m: score_state_value(m, context, t) for m in METRICS} for t in teams}
            scopes = context.creation_scopes
        else:
            result = execute_query(connection, query)
            if not isinstance(result, ScoreStateResult):
                raise ValueError("Expected score-state result")
            result = ScoreStateResult.model_validate_json(result.model_dump_json())
            if result.query != query:
                raise ValueError("Database returned a different creation query")
            if result.sources[0].events_sha256 != sha:
                raise ValueError("Database source differs from local reference")
            proof = result.score_evidence[0]
            if proof.goal_status != "resolved" or proof.final_score != tuple(
                score[t] for t in proof.team_ids
            ):
                raise ValueError("Database score differs from reference")
            observed = {r.team_id: {v.metric_id: v for v in r.values} for r in result.rows}
            scopes = {s.team_id: s for s in proof.creation_scope}
            database_sources = [s.model_dump(mode="json") for s in result.sources]
        if set(observed) != set(teams) or set(scopes) != set(teams):
            raise ValueError("Creation result is missing a match team")
        audit.append(
            {
                "score_state": state,
                "database_sources": database_sources,
                "creation_scope": [scope.model_dump(mode="json") for scope in scopes.values()],
            }
        )
        for team in teams:
            reference = expected[team, state]
            scope = scopes[team]
            if (
                scope.summary.pass_event_ids != reference["pass_event_ids"]
                or tuple(p.model_dump(mode="json") for p in scope.pairs) != reference["pairs"]
            ):
                raise ValueError(f"Creation pass/pair evidence differs: {team} {state}")
            for metric in METRICS:
                value = observed[team][metric]
                n = len(reference["pass_event_ids"])
                if (
                    value.value != reference["values"][metric]
                    or value.status != "available"
                    or not value.coverage.source_complete
                    or value.coverage.population != "presence_confirmed"
                    or value.coverage.eligible_events != n
                    or value.coverage.covered_events != n
                ):
                    raise ValueError(f"Creation mismatch {team} {state} {metric}")
                checks.append(
                    {
                        "team_id": team,
                        "score_state": state,
                        "metric_id": metric,
                        "value": value.value,
                        "status": value.status,
                        "eligible_passes": n,
                        "review_reasons": value.review_reasons,
                    }
                )
    return {
        "match_id": match["match_id"],
        "events_sha256": sha,
        "comparisons": len(checks),
        "checks": checks,
        "creation_evidence": audit,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events-dir", type=Path, required=True)
    parser.add_argument("--matches", type=Path, required=True)
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
                verify_match(args.events_dir / f"{match['match_id']}.json", match, connection)
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
                                    args.events_dir / f"{match['match_id']}.json", match, connection
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
        "reference_basis": "accepted_global_classifier_plus_independent_raw_pre_pass_score",
        "matches_sha256": hashlib.sha256(matches_raw).hexdigest(),
        "verified": verified,
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
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
