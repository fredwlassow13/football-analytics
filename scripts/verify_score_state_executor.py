"""Compare score-state scalar calculations with independent local raw counters."""

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from football_analytics.analytics.score_state_execution import (
    ScoreStateQuery,
    prepare_score_state,
    score_state_value,
)

METRICS = (
    "passes.attempted",
    "shots.total",
    "shots.goals",
    "shots.xg",
    "shots.xg_available",
    "shots.missing_xg",
    "dribbles.attempted",
)


def verify(path: Path, match: dict) -> dict:
    raw = path.read_bytes()
    payload = json.loads(raw)
    team_ids = tuple(str(match[f"{s}_team"][f"{s}_team_id"]) for s in ("home", "away"))
    teams = (team_ids[0], team_ids[1])
    score = dict.fromkeys(teams, 0)
    groups = {(team, state): [] for team in teams for state in ("leading", "drawing", "trailing")}
    for event in sorted(payload, key=lambda e: e["index"]):
        if event["period"] == 5:
            continue
        team = str(event.get("team", {}).get("id"))
        kind = event["type"]["name"]
        if team in teams:
            other = next(t for t in teams if t != team)
            delta = score[team] - score[other]
            state = "leading" if delta > 0 else "trailing" if delta < 0 else "drawing"
            groups[team, state].append(event)
            if kind == "Shot" and event["shot"]["outcome"]["name"] == "Goal":
                score[team] += 1
            elif kind == "Own Goal Against":
                score[other] += 1
    assert (score[teams[0]], score[teams[1]]) == (match["home_score"], match["away_score"])
    comparisons = 0
    for state in ("leading", "drawing", "trailing"):
        query = ScoreStateQuery(
            match_ids=(match["match_id"],), group_by="team", metrics=METRICS, score_state=state
        )
        context = prepare_score_state(
            json.loads(raw),
            query=query,
            match_id=match["match_id"],
            team_ids=teams,
            events_sha256=hashlib.sha256(raw).hexdigest(),
        )
        assert context.evolution.goal_evidence.status == "resolved"
        assert context.evolution.events[-1].score_after == (score[teams[0]], score[teams[1]])
        for team in teams:
            events = groups[team, state]
            shots = [e for e in events if e["type"]["name"] == "Shot"]
            xg = [
                Decimal(str(e["shot"]["statsbomb_xg"]))
                for e in shots
                if e["shot"].get("statsbomb_xg") is not None
            ]
            missing = len(shots) - len(xg)
            expected = {
                "passes.attempted": sum(e["type"]["name"] == "Pass" for e in events),
                "shots.total": len(shots),
                "shots.goals": sum(e["shot"]["outcome"]["name"] == "Goal" for e in shots),
                "shots.xg": None if missing else sum(xg, Decimal(0)),
                "shots.xg_available": sum(xg, Decimal(0)) if xg or not shots else None,
                "shots.missing_xg": missing,
                "dribbles.attempted": sum(e["type"]["name"] == "Dribble" for e in events),
            }
            for metric in METRICS:
                value = score_state_value(metric, context, team)
                if value.value != expected[metric]:
                    raise ValueError(
                        f"{team} {state} {metric}: {value.value} != {expected[metric]}"
                    )
                assert value.status == (
                    "incomplete"
                    if metric in {"shots.xg", "shots.xg_available"} and missing
                    else "available"
                )
                comparisons += 1
    return {
        "match_id": match["match_id"],
        "events_sha256": hashlib.sha256(raw).hexdigest(),
        "comparisons": comparisons,
        "score": (score[teams[0]], score[teams[1]]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events-dir", type=Path, required=True)
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a new destination")
    matches_raw = args.matches.read_bytes()
    verified, failures = [], []
    for match in json.loads(matches_raw):
        try:
            verified.append(verify(args.events_dir / f"{match['match_id']}.json", match))
        except (ValueError, AssertionError, OSError) as exc:
            failures.append(
                {"match_id": match["match_id"], "error": str(exc) or type(exc).__name__}
            )
    report = {
        "verification_basis": "independent_local_raw_events_not_database",
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
                "comparisons": sum(r["comparisons"] for r in verified),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
