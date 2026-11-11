from football_analytics.ingestion.match_participation import PlayerParticipation
from football_analytics.ingestion.tactical_timeline import (
    TacticalSegment,
    build_tactical_segment,
)
from football_analytics.integrations.base import MatchTacticalSnapshot


def build_team_tactical_timeline(
    *,
    timed_snapshots: list[tuple[int, MatchTacticalSnapshot]],
    players: list[PlayerParticipation],
    match_duration_ms: int,
) -> list[TacticalSegment]:
    if match_duration_ms <= 0:
        raise ValueError("Match duration must be positive")

    if not timed_snapshots or not players:
        raise ValueError("Snapshots and participation are required")

    ordered = sorted(
        timed_snapshots,
        key=lambda item: item[1].source_index,
    )
    initial_time, initial = ordered[0]

    if initial_time != 0 or initial.event_type != "Starting XI":
        raise ValueError("Timeline must start with Starting XI at zero")

    if initial.external_team_id is None:
        raise ValueError("Timeline requires a team")

    identity = (
        initial.provider,
        initial.external_match_id,
        initial.external_team_id,
    )
    roster: dict[str, PlayerParticipation] = {}

    for player in players:
        if (
            player.external_match_id != initial.external_match_id
            or player.external_team_id != initial.external_team_id
        ):
            raise ValueError("Participation belongs to another match or team")

        if player.external_player_id in roster:
            raise ValueError("Repeated player in participation")

        roster[player.external_player_id] = player

    boundaries = {0, match_duration_ms}
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    previous_time = 0

    for position, (elapsed_ms, snapshot) in enumerate(ordered):
        if (
            snapshot.provider,
            snapshot.external_match_id,
            snapshot.external_team_id,
        ) != identity:
            raise ValueError("Snapshots belong to different sources, matches or teams")

        if snapshot.period not in {1, 2, 3, 4}:
            raise ValueError("Timeline requires playing periods")

        if position > 0 and snapshot.event_type != "Tactical Shift":
            raise ValueError("Starting XI may appear only at timeline start")

        if snapshot.external_event_id in seen_ids:
            raise ValueError("Repeated tactical event ID")

        if snapshot.source_index in seen_indexes:
            raise ValueError("Repeated tactical event index")

        if not (previous_time <= elapsed_ms <= match_duration_ms):
            raise ValueError("Invalid tactical event time or backwards clock")

        if any(player.external_player_id not in roster for player in snapshot.lineup):
            raise ValueError("Tactical snapshot references an unlisted player")

        seen_ids.add(snapshot.external_event_id)
        seen_indexes.add(snapshot.source_index)
        previous_time = elapsed_ms
        boundaries.add(elapsed_ms)

    intervals_by_player: dict[str, list[tuple[int, int]]] = {}

    for player_id, player in roster.items():
        intervals = sorted((interval.start_ms, interval.end_ms) for interval in player.intervals)
        previous_end = 0

        for start, end in intervals:
            if not (0 <= start <= end <= match_duration_ms):
                raise ValueError("Invalid participation interval")

            if start < previous_end:
                raise ValueError("Overlapping participation intervals")

            previous_end = end
            boundaries.update((start, end))

        intervals_by_player[player_id] = intervals

    times = sorted(boundaries)
    result: list[TacticalSegment] = []
    snapshot_position = 0

    for start, end in zip(times, times[1:], strict=False):
        while snapshot_position + 1 < len(ordered) and ordered[snapshot_position + 1][0] <= start:
            snapshot_position += 1

        snapshot_time, snapshot = ordered[snapshot_position]
        active_player_ids = {
            player_id
            for player_id, intervals in intervals_by_player.items()
            if any(
                interval_start <= start < interval_end for interval_start, interval_end in intervals
            )
        }

        result.append(
            build_tactical_segment(
                snapshot=snapshot,
                snapshot_elapsed_ms=snapshot_time,
                active_player_ids=active_player_ids,
                start_elapsed_ms=start,
                end_elapsed_ms=end,
            )
        )

    return result
