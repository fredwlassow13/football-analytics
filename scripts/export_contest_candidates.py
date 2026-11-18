"""Export contest candidates for independent review, without assigning durations."""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from pydantic import TypeAdapter

from football_analytics.ingestion.attack_sequences import build_attack_sequences
from football_analytics.ingestion.contest_candidates import (
    EventAnnotation,
    build_contest_candidates,
)
from football_analytics.ingestion.possession_time import estimate_possession_time
from football_analytics.ingestion.possession_views import (
    ReviewedContestWindow,
    build_possession_views,
)
from football_analytics.integrations.base import MatchFoulCommitted, MatchFoulWon
from football_analytics.integrations.statsbomb.event_context import normalize_event_context
from football_analytics.integrations.statsbomb.passes import normalize_passes
from football_analytics.integrations.statsbomb.period_clock import normalize_period_clock
from football_analytics.integrations.statsbomb.play_metadata import normalize_event_play_metadata
from football_analytics.integrations.statsbomb.pressures_and_fouls import (
    normalize_pressures_and_fouls,
)
from football_analytics.integrations.statsbomb.shots import normalize_shots


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--match-id", type=int, required=True)
    parser.add_argument("--team-ids", nargs=2, required=True)
    parser.add_argument("--max-gap-ms", type=int, default=5000)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-window-ms", type=int, default=5000)
    parser.add_argument("--context-radius", type=int, default=3)
    parser.add_argument("--contests", type=Path, help="JSON array of reviewed contest windows")
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("Output directory exists; choose a new destination")
    raw = args.events.read_bytes()
    payload = json.loads(raw)
    contexts = normalize_event_context(payload, match_id=args.match_id)
    clock = normalize_period_clock(payload)
    fouls = [
        a
        for a in normalize_pressures_and_fouls(payload, match_id=args.match_id)
        if isinstance(a, (MatchFoulCommitted, MatchFoulWon))
    ]
    attacks = build_attack_sequences(
        contexts,
        normalize_event_play_metadata(payload, match_id=args.match_id),
        normalize_passes(payload, match_id=args.match_id),
        normalize_shots(payload, match_id=args.match_id),
        fouls,
        clock,
        provider="statsbomb",
        match_id=str(args.match_id),
        events_sha256=hashlib.sha256(raw).hexdigest(),
    )
    result = estimate_possession_time(
        contexts, attacks, clock, team_ids=tuple(args.team_ids), max_gap_ms=args.max_gap_ms
    )
    contests = (
        ()
        if args.contests is None
        else TypeAdapter(tuple[ReviewedContestWindow, ...]).validate_json(
            args.contests.read_bytes()
        )
    )
    report = build_possession_views(result, contests=contests, contexts=contexts)
    annotations = {}
    for source in payload:
        kind = source["type"]["name"]
        detail_key = {
            "Duel": "duel",
            "50/50": "50_50",
            "Pass": "pass",
            "Dribble": "dribble",
            "Ball Receipt*": "ball_receipt",
        }.get(kind)
        details = source.get(detail_key, {}) if detail_key else {}
        annotations[source["id"]] = EventAnnotation(
            event_type=kind,
            duel_type=details.get("type", {}).get("name") if kind == "Duel" else None,
            outcome=details.get("outcome", {}).get("name"),
            related_event_ids=(
                tuple(source["related_events"]) if "related_events" in source else None
            ),
            source_duration_seconds=source.get("duration"),
        )
    candidates = build_contest_candidates(
        contexts,
        report,
        events_sha256=hashlib.sha256(raw).hexdigest(),
        max_window_ms=args.max_window_ms,
        context_radius=args.context_radius,
        event_annotations=annotations,
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "candidates.json").write_text(candidates.model_dump_json(indent=2) + "\n")
    columns = [
        "candidate_id",
        "period",
        "seed_timestamp_ms",
        "seed_type",
        "team_id",
        "suggested_start_ms",
        "suggested_end_ms",
        "previous_proxy_id",
        "next_proxy_id",
        "overlaps_known_time",
        "overlapping_candidate_ids",
        "issues",
        "duel_type",
        "outcome",
        "related_event_ids",
        "paired_perspective_candidate_ids",
        "possession_aligned_proxy_id",
        "alternative_control_proxy_ids",
        "unclipped_end_ms",
        "known_stoppage_limit_ms",
        "crosses_known_stoppage",
    ]
    with (args.output_dir / "candidates.csv").open("x", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for candidate in candidates.candidates:
            writer.writerow(
                {
                    "candidate_id": candidate.candidate_id,
                    "period": candidate.seed.period,
                    "seed_timestamp_ms": candidate.seed.timestamp_ms,
                    "seed_type": candidate.seed.event_type,
                    "team_id": candidate.seed.external_team_id,
                    "suggested_start_ms": candidate.suggested_start_ms,
                    "suggested_end_ms": candidate.suggested_end_ms,
                    "previous_proxy_id": (
                        candidate.preceding_control_proxy.external_event_id
                        if candidate.preceding_control_proxy
                        else None
                    ),
                    "next_proxy_id": (
                        candidate.following_boundary_proxy.external_event_id
                        if candidate.following_boundary_proxy
                        else None
                    ),
                    "overlaps_known_time": candidate.overlaps_known_time,
                    "overlapping_candidate_ids": "|".join(candidate.overlapping_candidate_ids),
                    "issues": "|".join(candidate.issues),
                    "duel_type": candidate.seed.duel_type,
                    "outcome": candidate.seed.outcome,
                    "related_event_ids": "|".join(candidate.seed.related_event_ids or ()),
                    "paired_perspective_candidate_ids": "|".join(
                        candidate.paired_perspective_candidate_ids
                    ),
                    "possession_aligned_proxy_id": (
                        candidate.following_possession_aligned_proxy.external_event_id
                        if candidate.following_possession_aligned_proxy
                        else None
                    ),
                    "alternative_control_proxy_ids": "|".join(
                        e.external_event_id
                        for e in candidate.control_proxies_before_aligned_boundary
                    ),
                    "unclipped_end_ms": candidate.unclipped_end_ms,
                    "known_stoppage_limit_ms": candidate.known_stoppage_limit_ms,
                    "crosses_known_stoppage": candidate.crosses_known_stoppage,
                }
            )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "seeds": len(candidates.candidates),
                "seed_types": dict(Counter(c.seed.event_type for c in candidates.candidates)),
                "with_suggested_bounds": sum(
                    c.suggested_end_ms is not None for c in candidates.candidates
                ),
                "overlaps_known_time": sum(c.overlaps_known_time for c in candidates.candidates),
                "overlapping_candidates": sum(
                    bool(c.overlapping_candidate_ids) for c in candidates.candidates
                ),
                "excluded_shootout_seeds": len(candidates.excluded_shootout_seed_ids),
                "accepted_contest_windows": 0,
                "with_alternative_control_proxies": sum(
                    bool(c.control_proxies_before_aligned_boundary) for c in candidates.candidates
                ),
                "limited_by_known_stoppage": sum(
                    c.crosses_known_stoppage for c in candidates.candidates
                ),
                "paired_perspective_seeds": sum(
                    bool(c.paired_perspective_candidate_ids) for c in candidates.candidates
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
