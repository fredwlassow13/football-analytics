"""Inspect the conservative possession-time estimate from an explicit local source."""

import argparse
import hashlib
import json
from pathlib import Path

from pydantic import TypeAdapter

from football_analytics.ingestion.attack_sequences import build_attack_sequences
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
    parser.add_argument("--output", type=Path)
    parser.add_argument("--contests", type=Path, help="JSON array of reviewed contest windows")
    args = parser.parse_args()
    if args.output is not None and args.output.exists():
        parser.error("Output exists; choose a new destination")
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
    if args.output is not None:
        with args.output.open("x") as handle:
            handle.write(report.model_dump_json(indent=2) + "\n")
    print(report.model_dump_json(indent=2, exclude={"intervals"}))


if __name__ == "__main__":
    main()
