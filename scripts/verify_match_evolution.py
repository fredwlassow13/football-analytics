"""Reconcile local match evolution with independent raw-event score/xG counters."""

import argparse
import hashlib
import json
from collections import Counter
from math import fsum
from pathlib import Path

from football_analytics.visualization.match_evolution import evolution_from_payload


def verify(events_path: Path, match: dict) -> dict:
    raw = events_path.read_bytes()
    payload = json.loads(raw)
    teams = tuple(str(match[f"{s}_team"][f"{s}_team_id"]) for s in ("home", "away"))
    result = evolution_from_payload(
        payload,
        match_id=match["match_id"],
        team_ids=(teams[0], teams[1]),
        events_sha256=hashlib.sha256(raw).hexdigest(),
    )
    playing = sorted((e for e in payload if e["period"] != 5), key=lambda e: e["index"])
    raw_shots = [e for e in playing if e["type"]["name"] == "Shot"]
    # Own Goal Against is one perspective per scoring episode in this provider.
    # Compare score states of offensive actions, independent of normalized goal links.
    goals = [
        e
        for e in playing
        if e["type"]["name"] == "Own Goal Against"
        or (e["type"]["name"] == "Shot" and e["shot"]["outcome"]["name"] == "Goal")
    ]
    score = Counter()
    prior = {}
    for e in playing:
        prior[e["id"]] = tuple(score[t] for t in teams)
        if e in goals:
            team = str(e["team"]["id"])
            if e["type"]["name"] == "Own Goal Against":
                team = next(t for t in teams if t != team)
            score[team] += 1
    expected = (match["home_score"], match["away_score"])
    actual = tuple(score[t] for t in teams)
    if actual != expected:
        raise ValueError(f"Raw score differs: {match['match_id']}: {actual} vs {expected}")
    if result.goal_evidence.status != "resolved":
        raise ValueError(f"Unresolved normalized goal evidence: {match['match_id']}")
    if not result.events or result.events[-1].score_after != expected:
        raise ValueError("Normalized final score differs")
    for row in result.events:
        if (
            row.event_type in {"Pass", "Shot", "Carry"}
            and row.score_before != prior[row.external_event_id]
        ):
            raise ValueError(
                f"Raw and normalized pre-action scores differ: {row.external_event_id}"
            )
    for team in teams:
        rows = [e for e in raw_shots if str(e["team"]["id"]) == team]
        groups = [g for g in result.by_score_state if g.external_team_id == team]
        if sum(g.shots for g in groups) != len(rows):
            raise ValueError("Shot groups do not recompose raw population")
        for group in groups:
            selected = []
            i = teams.index(team)
            for e in rows:
                before = prior[e["id"]]
                delta = before[i] - before[1 - i]
                state = "leading" if delta > 0 else "trailing" if delta < 0 else "drawing"
                if state == group.score_state:
                    selected.append(e)
            values = [
                e["shot"]["statsbomb_xg"]
                for e in selected
                if e["shot"].get("statsbomb_xg") is not None
            ]
            if (
                tuple(e["id"] for e in selected) != group.shot_event_ids
                or abs(fsum(values) - group.xg_available) > 1e-12
                or group.missing_xg != len(selected) - len(values)
            ):
                raise ValueError("Raw score-state shot/xG coverage differs")
    return {
        "match_id": match["match_id"],
        "events_sha256": result.events_sha256,
        "score": actual,
        "events": len(playing),
        "shots": len(raw_shots),
        "own_goals": sum(e["type"]["name"] == "Own Goal Against" for e in goals),
        "usable_creation_pairs": sum(g.created_shots for g in result.by_score_state),
        "issues": result.issues,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events-dir", type=Path, required=True)
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--match-id", type=int, action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a new destination")
    matches = json.loads(args.matches.read_bytes())
    if args.match_id:
        matches = [m for m in matches if m["match_id"] in args.match_id]
        if {m["match_id"] for m in matches} != set(args.match_id):
            parser.error("Requested match missing from source")
    results, failures = [], []
    for match in matches:
        try:
            results.append(verify(args.events_dir / f"{match['match_id']}.json", match))
        except (ValueError, OSError) as exc:
            failures.append({"match_id": match["match_id"], "error": str(exc)})
    report = {
        "policy_version": "1.0.0",
        "verification_basis": "independent_local_raw_events",
        "matches_sha256": hashlib.sha256(args.matches.read_bytes()).hexdigest(),
        "verified": results,
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "verified": len(results),
                "failures": failures,
                "own_goal_matches": sum(r["own_goals"] > 0 for r in results),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
