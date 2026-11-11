import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import MatchTacticalSnapshot


def snapshot_data() -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "tactics-1",
        "source_index": 1,
        "period": 1,
        "timestamp_ms": 0,
        "event_type": "Starting XI",
        "external_team_id": "779",
        "formation": "433",
        "lineup": [
            {
                "external_player_id": str(index),
                "external_position_id": str(index),
                "position_name": f"Position {index}",
            }
            for index in range(1, 12)
        ],
    }


@pytest.mark.parametrize("event_type", ["Starting XI", "Tactical Shift"])
def test_accepts_tactical_event_types(event_type: str) -> None:
    data = snapshot_data()
    data["event_type"] = event_type

    result = MatchTacticalSnapshot.model_validate(data)

    assert result.event_type == event_type
    assert result.formation == "433"
    assert result.external_team_id == "779"
    assert len(result.lineup) == 11
    assert result.lineup[0].external_player_id == "1"
    assert result.lineup[0].external_position_id == "1"
    assert result.lineup[0].position_name == "Position 1"


def test_preserves_distinct_events_with_same_formation() -> None:
    first = MatchTacticalSnapshot.model_validate(snapshot_data())

    data = snapshot_data()
    data.update(
        external_event_id="tactics-2",
        source_index=100,
        timestamp_ms=120_000,
        event_type="Tactical Shift",
    )
    second = MatchTacticalSnapshot.model_validate(data)

    assert first.formation == second.formation
    assert first.external_event_id != second.external_event_id
    assert second.timestamp_ms == 120_000


def test_accepts_lineup_with_fewer_than_eleven_players() -> None:
    data = snapshot_data()
    data["lineup"] = [
        {
            "external_player_id": str(index),
            "external_position_id": str(index),
            "position_name": f"Position {index}",
        }
        for index in range(1, 11)
    ]

    result = MatchTacticalSnapshot.model_validate(data)

    assert len(result.lineup) == 10


def test_rejects_duplicate_players() -> None:
    data = snapshot_data()
    player = {
        "external_player_id": "1",
        "external_position_id": "1",
        "position_name": "Goalkeeper",
    }
    data["lineup"] = [player, player]

    with pytest.raises(ValidationError, match="duplicate players"):
        MatchTacticalSnapshot.model_validate(data)


@pytest.mark.parametrize("size", [0, 12])
def test_rejects_invalid_lineup_size(size: int) -> None:
    data = snapshot_data()
    data["lineup"] = [
        {
            "external_player_id": str(index),
            "external_position_id": str(index),
            "position_name": f"Position {index}",
        }
        for index in range(1, size + 1)
    ]

    with pytest.raises(ValidationError):
        MatchTacticalSnapshot.model_validate(data)


@pytest.mark.parametrize("formation", ["", None, 433])
def test_requires_nonempty_string_formation(formation: object) -> None:
    data = snapshot_data()
    data["formation"] = formation

    with pytest.raises(ValidationError):
        MatchTacticalSnapshot.model_validate(data)


def test_rejects_other_event_type() -> None:
    data = snapshot_data()
    data["event_type"] = "Substitution"

    with pytest.raises(ValidationError):
        MatchTacticalSnapshot.model_validate(data)


@pytest.mark.parametrize(
    "field",
    ["external_player_id", "external_position_id", "position_name"],
)
def test_rejects_empty_player_reference_fields(field: str) -> None:
    data = snapshot_data()
    player = {
        "external_player_id": "1",
        "external_position_id": "1",
        "position_name": "Goalkeeper",
    }
    player[field] = ""
    data["lineup"] = [player]

    with pytest.raises(ValidationError):
        MatchTacticalSnapshot.model_validate(data)
