import pytest

from football_analytics.ingestion.tactical_timeline import build_tactical_segment
from football_analytics.integrations.base import MatchTacticalSnapshot


def make_snapshot(
    *,
    event_id: str = "initial",
    index: int = 1,
    timestamp_ms: int = 0,
    second_player: str = "player-b",
) -> MatchTacticalSnapshot:
    return MatchTacticalSnapshot.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_event_id": event_id,
            "source_index": index,
            "period": 1,
            "timestamp_ms": timestamp_ms,
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


def test_substitution_leaves_incoming_position_unknown_until_new_snapshot() -> None:
    initial = make_snapshot()
    updated = make_snapshot(
        event_id="updated",
        index=3,
        timestamp_ms=8000,
        second_player="player-c",
    )

    before = build_tactical_segment(
        snapshot=initial,
        snapshot_elapsed_ms=0,
        active_player_ids={"player-a", "player-b"},
        start_elapsed_ms=0,
        end_elapsed_ms=5000,
    )
    after_substitution = build_tactical_segment(
        snapshot=initial,
        snapshot_elapsed_ms=0,
        active_player_ids={"player-a", "player-c"},
        start_elapsed_ms=5000,
        end_elapsed_ms=8000,
    )
    after_snapshot = build_tactical_segment(
        snapshot=updated,
        snapshot_elapsed_ms=8000,
        active_player_ids={"player-a", "player-c"},
        start_elapsed_ms=8000,
        end_elapsed_ms=10_000,
    )

    assert [player.external_player_id for player in before.players] == [
        "player-a",
        "player-b",
    ]

    middle_players = {player.external_player_id: player for player in after_substitution.players}
    assert set(middle_players) == {"player-a", "player-c"}
    assert middle_players["player-c"].last_recorded_position is None
    assert after_substitution.formation_source_event_id == "initial"

    final_players = {player.external_player_id: player for player in after_snapshot.players}
    position = final_players["player-c"].last_recorded_position
    assert position is not None
    assert position.position_name == "Center Forward"
    assert position.source_event_id == "updated"
    assert after_snapshot.formation_source_event_id == "updated"

    assert (
        sum(segment.duration_ms for segment in (before, after_substitution, after_snapshot))
        == 10_000
    )


def test_temporary_absence_preserves_evidence_for_returning_player() -> None:
    snapshot = make_snapshot()

    absent = build_tactical_segment(
        snapshot=snapshot,
        snapshot_elapsed_ms=0,
        active_player_ids={"player-a"},
        start_elapsed_ms=1000,
        end_elapsed_ms=2000,
    )
    returned = build_tactical_segment(
        snapshot=snapshot,
        snapshot_elapsed_ms=0,
        active_player_ids={"player-a", "player-b"},
        start_elapsed_ms=2000,
        end_elapsed_ms=3000,
    )

    assert absent.player_count == 1
    assert returned.player_count == 2
    assert absent.last_recorded_formation == "433"
    assert returned.last_recorded_formation == "433"

    player = next(item for item in returned.players if item.external_player_id == "player-b")
    assert player.last_recorded_position is not None
    assert player.last_recorded_position.source_event_id == "initial"


@pytest.mark.parametrize("snapshot_elapsed_ms", [-1, 2000])
def test_rejects_invalid_snapshot_time(snapshot_elapsed_ms: int) -> None:
    with pytest.raises(ValueError, match="Snapshot"):
        build_tactical_segment(
            snapshot=make_snapshot(),
            snapshot_elapsed_ms=snapshot_elapsed_ms,
            active_player_ids={"player-a"},
            start_elapsed_ms=1000,
            end_elapsed_ms=3000,
        )


def test_rejects_missing_team() -> None:
    data = make_snapshot().model_dump()
    data["external_team_id"] = None
    snapshot = MatchTacticalSnapshot.model_validate(data)

    with pytest.raises(ValueError, match="requires a team"):
        build_tactical_segment(
            snapshot=snapshot,
            snapshot_elapsed_ms=0,
            active_player_ids={"player-a"},
            start_elapsed_ms=0,
            end_elapsed_ms=1000,
        )


def test_rejects_shootout_snapshot() -> None:
    data = make_snapshot().model_dump()
    data["period"] = 5
    snapshot = MatchTacticalSnapshot.model_validate(data)

    with pytest.raises(ValueError, match="playing period"):
        build_tactical_segment(
            snapshot=snapshot,
            snapshot_elapsed_ms=0,
            active_player_ids={"player-a"},
            start_elapsed_ms=0,
            end_elapsed_ms=1000,
        )
