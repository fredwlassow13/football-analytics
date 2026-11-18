"""Verify all defensive score-state metrics against independent raw-event counters."""

import argparse
import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
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
from football_analytics.infrastructure.database import get_engine

STATES = ("leading", "drawing", "trailing")
FAMILIES = frozenset({"defense", "duels", "aerial"})
DEFINITIONS = {m.id: m for m in CATALOGUE if m.family in FAMILIES}
METRICS = tuple(sorted(DEFINITIONS))
ACTION_TYPES = frozenset(
    {
        "Duel",
        "Interception",
        "Ball Recovery",
        "Block",
        "Clearance",
        "Pressure",
        "Dribbled Past",
        "Foul Committed",
        "Foul Won",
    }
)
DETAIL_KEYS = {
    "Ball Recovery": "ball_recovery",
    "Block": "block",
    "Clearance": "clearance",
    "Foul Committed": "foul_committed",
    "Foul Won": "foul_won",
}


def aerial_outcome(event: dict) -> str | None:
    kind = event["type"]["name"]
    if kind == "Duel" and event["duel"]["type"]["name"] == "Aerial Lost":
        return "lost"
    key = {
        "Pass": "pass",
        "Shot": "shot",
        "Miscontrol": "miscontrol",
        "Clearance": "clearance",
    }.get(kind)
    return "won" if key and event.get(key, {}).get("aerial_won") is True else None


def expected_scopes(
    payload: list[dict], match: dict, sha: str
) -> tuple[tuple[str, str], dict, dict]:
    """No production normalizer, defensive summary or timeline is used here."""
    ids = tuple(str(match[f"{side}_team"][f"{side}_team_id"]) for side in ("home", "away"))
    teams = (ids[0], ids[1])
    if len(set(teams)) != 2:
        raise ValueError("Two reference teams required")
    if len({e["id"] for e in payload}) != len(payload) or len({e["index"] for e in payload}) != len(
        payload
    ):
        raise ValueError("Duplicate raw source event")
    score = dict.fromkeys(teams, 0)
    groups: dict[tuple[str, str], list[dict]] = {(t, state): [] for t in teams for state in STATES}
    for event in sorted(payload, key=lambda e: e["index"]):
        if event["period"] == 5:
            continue
        if event["period"] not in (1, 2, 3, 4):
            raise ValueError("Invalid raw period")
        team = str(event.get("team", {}).get("id"))
        kind = event["type"]["name"]
        if team not in teams:
            if kind in ACTION_TYPES | {
                "Pass",
                "Shot",
                "Miscontrol",
                "Own Goal Against",
                "Own Goal For",
            }:
                raise ValueError("Reference action without a match team")
            continue
        other = next(t for t in teams if t != team)
        delta = score[team] - score[other]
        state = "leading" if delta > 0 else "trailing" if delta < 0 else "drawing"
        groups[team, state].append(event)
        if kind == "Shot" and event["shot"]["outcome"]["name"] == "Goal":
            score[team] += 1
        elif kind == "Own Goal Against":
            score[other] += 1
    if tuple(score[t] for t in teams) != (match["home_score"], match["away_score"]):
        raise ValueError("Raw score differs from match record")
    catalogue_ids = {event["id"] for event in payload}
    expected = {}
    for group, events in groups.items():
        actions = [e for e in events if e["type"]["name"] in ACTION_TYPES]
        aerials = [e for e in events if aerial_outcome(e) is not None]
        action_ids, aerial_ids = {e["id"] for e in actions}, {e["id"] for e in aerials}
        union = [e for e in events if e["id"] in action_ids | aerial_ids]
        duels = [e for e in actions if e["type"]["name"] == "Duel"]
        values = {}
        for metric, definition in DEFINITIONS.items():
            selection = dict(definition.selection)
            member = metric.rsplit(".", 1)[-1]
            status, population = "available", "presence_confirmed"
            numerator = denominator = None
            if definition.family == "defense":
                if metric == "defense.unique_sources":
                    number = n = len(union)
                else:
                    subset = [e for e in actions if e["type"]["name"] == selection["event_type"]]
                    n = len(subset)
                    if "flag" not in selection:
                        number = n
                    else:
                        flag = selection["flag"]
                        raw_flags = [
                            e.get("counterpress")
                            if flag == "counterpress"
                            else e.get(DETAIL_KEYS[selection["event_type"]], {}).get(flag)
                            for e in subset
                        ]
                        number = (
                            sum(v is True for v in raw_flags)
                            if member == "true"
                            else (
                                sum(v is False for v in raw_flags)
                                if member == "false"
                                else sum(v is None for v in raw_flags)
                            )
                        )
            elif definition.family == "duels":
                n = len(duels)
                tackles = [e for e in duels if e["duel"]["type"]["name"] == "Tackle"]
                if metric == "duels.total_duel_events":
                    number = n
                elif metric == "duels.aerial_lost":
                    number = sum(e["duel"]["type"]["name"] == "Aerial Lost" for e in duels)
                elif member == "total":
                    number = len(tackles)
                else:
                    number = sum(
                        e["duel"].get("outcome", {}).get("name", "").lower().replace(" ", "_")
                        == member
                        for e in tackles
                    )
            else:
                won = sum(aerial_outcome(e) == "won" for e in aerials)
                lost = len(aerials) - won
                n = len(aerials)
                if member == "win_pct":
                    if n:
                        numerator, denominator = won, n
                        number = (Decimal(won) * 100 / Decimal(n)).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )
                    else:
                        number, status, population = None, "not_applicable", "not_applicable"
                else:
                    number = {"won": won, "lost": lost, "contested": n}[member]
            values[metric] = {
                "value": number,
                "status": status,
                "population": population,
                "eligible_events": n,
                "covered_events": n,
                "numerator": numerator,
                "denominator": denominator,
            }
        related = {
            e["id"]: tuple(e["related_events"]) if "related_events" in e else None for e in union
        }
        targets = {target for ids in related.values() for target in ids or ()}
        union_ids = {e["id"] for e in union}
        expected[group] = {
            "values": values,
            "unique_ids": tuple(e["id"] for e in union),
            "action_ids": tuple(e["id"] for e in actions),
            "aerial_ids": tuple(e["id"] for e in aerials),
            "shared_ids": tuple(e["id"] for e in union if e["id"] in action_ids & aerial_ids),
            "related": related,
            "missing_targets": tuple(sorted(targets - catalogue_ids)),
            "outside_targets": tuple(sorted((targets & catalogue_ids) - union_ids)),
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
            score_state_contract_version="1.2.0",
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
            scopes = context.defensive_scopes
        else:
            result = execute_query(connection, query)
            if not isinstance(result, ScoreStateResult):
                raise ValueError("Expected score-state result")
            result = ScoreStateResult.model_validate_json(result.model_dump_json())
            if result.query != query:
                raise ValueError("Database returned a different defensive query")
            if result.sources[0].events_sha256 != sha:
                raise ValueError("Database source differs from local reference")
            proof = result.score_evidence[0]
            if proof.goal_status != "resolved" or proof.final_score != tuple(
                score[t] for t in proof.team_ids
            ):
                raise ValueError("Database score differs from reference")
            observed = {r.team_id: {v.metric_id: v for v in r.values} for r in result.rows}
            scopes = {s.team_id: s for s in proof.defensive_scope}
            database_sources = [s.model_dump(mode="json") for s in result.sources]
        if set(observed) != set(teams) or set(scopes) != set(teams):
            raise ValueError("Defensive result is missing a match team")
        audit.append(
            {
                "score_state": state,
                "database_sources": database_sources,
                "defensive_scope": [scope.model_dump(mode="json") for scope in scopes.values()],
            }
        )
        for team in teams:
            reference = expected[team, state]
            scope = scopes[team]
            observed_evidence = {
                "unique_ids": scope.unique_source_event_ids,
                "action_ids": scope.action_event_ids,
                "aerial_ids": scope.aerial_event_ids,
                "shared_ids": scope.shared_event_ids,
                "related": scope.related_ids_by_source,
                "missing_targets": scope.missing_related_target_ids,
                "outside_targets": scope.related_targets_outside_selected_union,
            }
            if observed_evidence != {k: v for k, v in reference.items() if k != "values"}:
                raise ValueError(f"Defensive source evidence differs: {team} {state}")
            for metric in METRICS:
                value = observed[team][metric]
                actual = {
                    "value": value.value,
                    "status": value.status,
                    "population": value.coverage.population,
                    "eligible_events": value.coverage.eligible_events,
                    "covered_events": value.coverage.covered_events,
                    "numerator": value.numerator,
                    "denominator": value.denominator,
                }
                if actual != reference["values"][metric] or not value.coverage.source_complete:
                    raise ValueError(
                        f"Defensive mismatch {team} {state} {metric}: "
                        f"{actual!r} != {reference['values'][metric]!r}"
                    )
                if (
                    reference["missing_targets"]
                    and "defensive_related_targets_missing" not in value.review_reasons
                ):
                    raise ValueError("Missing-target diagnostic was lost")
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
        "comparisons": len(checks),
        "checks": checks,
        "defensive_evidence": audit,
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
        "reference_basis": "independent_raw_event_fields_and_pre_action_score",
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
