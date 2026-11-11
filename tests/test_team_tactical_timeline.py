import pytest

from football_analytics.ingestion.match_participation import PlayerParticipation
from football_analytics.ingestion.participation import ParticipationInterval
from football_analytics.ingestion.team_tactical_timeline import (
    build_team_tactical_timeline,
)
from football_analytics.integrations.base import MatchTacticalSnapshot


def make_snapshot(
    *,
    index: int = 1,
    second_player: str = "player-b",
) -> MatchTacticalSnapshot:
    return MatchTacticalSnapshot.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "match-1",
            "external_event_id": f"tactics-{index}",
            "source_index": index,
            "period": 1,
            "timestamp_ms": 0,
            "event_type": "Starting XI" if index == 1 else "Tactical Shift",
            "external_team_id": "team-1",
            "formation": "433",
            "lineup": [
                {
                    "external_player_id": "player-a",
                    "external_position_id": "17",
                    "position_name": "Right Wing",
                },
                {
                    "external_player_id": second_player,
                    "external_position_id": "23",
                    "position_name": "Center Forward",
                },
            ],
        }
    )


def make_player(
    player_id: str,
    intervals: list[tuple[int, int]],
) -> PlayerParticipation:
    return PlayerParticipation(
        external_match_id="match-1",
        external_team_id="team-1",
        external_player_id=player_id,
        is_starter=None,
        intervals=[ParticipationInterval(start_ms=start, end_ms=end) for start, end in intervals],
        elapsed_on_field_ms=(sum(end - start for start, end in intervals) if intervals else None),
    )


def test_builds_substitution_and_tactical_update_segments() -> None:
    result = build_team_tactical_timeline(
        timed_snapshots=[
            (8000, make_snapshot(index=3, second_player="player-c")),
            (0, make_snapshot()),
        ],
        players=[
            make_player("player-a", [(0, 10_000)]),
            make_player("player-b", [(0, 5000)]),
            make_player("player-c", [(5000, 10_000)]),
        ],
        match_duration_ms=10_000,
    )

    assert [(segment.start_elapsed_ms, segment.end_elapsed_ms) for segment in result] == [
        (0, 5000),
        (5000, 8000),
        (8000, 10_000),
    ]

    middle = {player.external_player_id: player for player in result[1].players}
    assert set(middle) == {"player-a", "player-c"}
    assert middle["player-c"].last_recorded_position is None

    final = {player.external_player_id: player for player in result[2].players}
    position = final["player-c"].last_recorded_position
    assert position is not None
    assert position.source_event_id == "tactics-3"

    assert sum(segment.duration_ms for segment in result) == 10_000
    assert sum(segment.duration_ms * segment.player_count for segment in result) == 20_000


def test_handles_temporary_absence_and_excludes_player_without_intervals() -> None:
    result = build_team_tactical_timeline(
        timed_snapshots=[(0, make_snapshot())],
        players=[
            make_player("player-a", [(0, 2000), (3000, 10_000)]),
            make_player("player-b", [(0, 10_000)]),
            make_player("unused-player", []),
        ],
        match_duration_ms=10_000,
    )

    assert [segment.player_count for segment in result] == [2, 1, 2]
    assert all(
        player.external_player_id != "unused-player"
        for segment in result
        for player in segment.players
    )
    assert sum(segment.duration_ms * segment.player_count for segment in result) == 19_000


def test_uses_last_source_index_for_simultaneous_snapshots() -> None:
    result = build_team_tactical_timeline(
        timed_snapshots=[
            (0, make_snapshot()),
            (5000, make_snapshot(index=2)),
            (5000, make_snapshot(index=3)),
        ],
        players=[
            make_player("player-a", [(0, 10_000)]),
            make_player("player-b", [(0, 10_000)]),
        ],
        match_duration_ms=10_000,
    )

    assert len(result) == 2
    assert result[1].formation_source_event_id == "tactics-3"


def test_rejects_backwards_snapshot_clock() -> None:
    with pytest.raises(ValueError, match="backwards clock"):
        build_team_tactical_timeline(
            timed_snapshots=[
                (0, make_snapshot()),
                (8000, make_snapshot(index=2)),
                (7000, make_snapshot(index=3)),
            ],
            players=[
                make_player("player-a", [(0, 10_000)]),
                make_player("player-b", [(0, 10_000)]),
            ],
            match_duration_ms=10_000,
        )


def test_rejects_overlapping_participation() -> None:
    with pytest.raises(ValueError, match="Overlapping"):
        build_team_tactical_timeline(
            timed_snapshots=[(0, make_snapshot())],
            players=[
                make_player("player-a", [(0, 6000), (5000, 10_000)]),
                make_player("player-b", [(0, 10_000)]),
            ],
            match_duration_ms=10_000,
        )


def test_rejects_unlisted_tactical_player() -> None:
    with pytest.raises(ValueError, match="unlisted player"):
        build_team_tactical_timeline(
            timed_snapshots=[(0, make_snapshot())],
            players=[make_player("player-a", [(0, 10_000)])],
            match_duration_ms=10_000,
        )
