import pytest

from football_analytics.ingestion.tactical_changes import (
    compare_tactical_snapshots,
)
from football_analytics.integrations.base import MatchTacticalSnapshot


def snapshot_data(index: int) -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": f"tactics-{index}",
        "source_index": index,
        "period": 1,
        "timestamp_ms": index * 1000,
        "event_type": "Tactical Shift",
        "external_team_id": "team-1",
        "formation": "433",
        "lineup": [
            {
                "external_player_id": "player-1",
                "external_position_id": "17",
                "position_name": "Right Wing",
            },
            {
                "external_player_id": "player-2",
                "external_position_id": "21",
                "position_name": "Left Wing",
            },
        ],
    }


def test_detects_replacement_and_position_change_without_formation_change() -> None:
    previous = MatchTacticalSnapshot.model_validate(snapshot_data(1))
    data = snapshot_data(2)
    data["lineup"] = [
        {
            "external_player_id": "player-1",
            "external_position_id": "23",
            "position_name": "Center Forward",
        },
        {
            "external_player_id": "player-3",
            "external_position_id": "21",
            "position_name": "Left Wing",
        },
    ]
    current = MatchTacticalSnapshot.model_validate(data)

    changes = compare_tactical_snapshots(previous, current)

    assert changes.previous_event_id == "tactics-1"
    assert changes.current_event_id == "tactics-2"
    assert changes.formation_changed is False
    assert [player.external_player_id for player in changes.added] == ["player-3"]
    assert [player.external_player_id for player in changes.removed] == ["player-2"]
    assert len(changes.position_changes) == 1
    change = changes.position_changes[0]
    assert change.previous.external_player_id == "player-1"
    assert change.current.external_player_id == "player-1"
    assert change.previous.position_name == "Right Wing"
    assert change.current.position_name == "Center Forward"


def test_detects_formation_change_independently_of_lineup() -> None:
    previous = MatchTacticalSnapshot.model_validate(snapshot_data(1))
    data = snapshot_data(2)
    data["formation"] = "442"
    current = MatchTacticalSnapshot.model_validate(data)

    changes = compare_tactical_snapshots(previous, current)

    assert changes.formation_changed is True
    assert changes.previous_formation == "433"
    assert changes.current_formation == "442"
    assert changes.added == []
    assert changes.removed == []
    assert changes.position_changes == []


def test_preserves_comparison_of_identical_lineups() -> None:
    previous = MatchTacticalSnapshot.model_validate(snapshot_data(1))
    current = MatchTacticalSnapshot.model_validate(snapshot_data(2))

    changes = compare_tactical_snapshots(previous, current)

    assert changes.formation_changed is False
    assert changes.added == []
    assert changes.removed == []
    assert changes.position_changes == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "another-provider"),
        ("external_match_id", "another-match"),
        ("external_team_id", "another-team"),
    ],
)
def test_rejects_mixed_identities(field: str, value: str) -> None:
    previous = MatchTacticalSnapshot.model_validate(snapshot_data(1))
    data = snapshot_data(2)
    data[field] = value
    current = MatchTacticalSnapshot.model_validate(data)

    with pytest.raises(ValueError, match="one provider, match and team"):
        compare_tactical_snapshots(previous, current)


def test_rejects_missing_team() -> None:
    first = snapshot_data(1)
    second = snapshot_data(2)
    first["external_team_id"] = None
    second["external_team_id"] = None

    with pytest.raises(ValueError, match="requires a team"):
        compare_tactical_snapshots(
            MatchTacticalSnapshot.model_validate(first),
            MatchTacticalSnapshot.model_validate(second),
        )


@pytest.mark.parametrize("current_index", [1, 2])
def test_rejects_nonincreasing_source_index(current_index: int) -> None:
    previous = MatchTacticalSnapshot.model_validate(snapshot_data(2))
    data = snapshot_data(current_index)
    data["external_event_id"] = "distinct-event"
    current = MatchTacticalSnapshot.model_validate(data)

    with pytest.raises(ValueError, match="must follow"):
        compare_tactical_snapshots(previous, current)


def test_rejects_same_event_identity() -> None:
    previous = MatchTacticalSnapshot.model_validate(snapshot_data(1))
    data = snapshot_data(2)
    data["external_event_id"] = previous.external_event_id
    current = MatchTacticalSnapshot.model_validate(data)

    with pytest.raises(ValueError, match="distinct events"):
        compare_tactical_snapshots(previous, current)


def test_accepts_period_transition_with_local_clock_reset() -> None:
    previous = MatchTacticalSnapshot.model_validate(snapshot_data(1))
    data = snapshot_data(2)
    data.update(period=2, timestamp_ms=0)
    current = MatchTacticalSnapshot.model_validate(data)

    changes = compare_tactical_snapshots(previous, current)

    assert changes.current_event_id == "tactics-2"
