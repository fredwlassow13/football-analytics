import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import MatchEventContext


def context_data() -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "event-1",
        "source_index": 100,
        "period": 1,
        "timestamp_ms": 120_000,
        "event_type": "Pass",
        "external_team_id": "team-1",
        "external_player_id": "player-1",
    }


def test_preserves_missing_context() -> None:
    event = MatchEventContext.model_validate(context_data())

    assert event.position is None
    assert event.possession_sequence is None
    assert event.external_possession_team_id is None
    assert event.under_pressure is None
    assert event.event_team_has_possession is None


def test_preserves_position_and_event_identity() -> None:
    data = context_data()
    data["position"] = {
        "external_position_id": "19",
        "position_name": "Center Attacking Midfield",
    }

    event = MatchEventContext.model_validate(data)

    assert event.external_event_id == "event-1"
    assert event.source_index == 100
    assert event.timestamp_ms == 120_000
    assert event.position is not None
    assert event.position.external_position_id == "19"
    assert event.position.position_name == "Center Attacking Midfield"


@pytest.mark.parametrize(
    ("event_team", "possession_team", "expected"),
    [
        ("team-1", "team-1", True),
        ("team-1", "team-2", False),
        ("team-1", None, None),
        (None, "team-1", None),
    ],
)
def test_compares_event_team_with_possession_team(
    event_team: str | None,
    possession_team: str | None,
    expected: bool | None,
) -> None:
    data = context_data()
    data.update(
        external_team_id=event_team,
        external_possession_team_id=possession_team,
        possession_sequence=10,
    )

    event = MatchEventContext.model_validate(data)

    assert event.possession_sequence == 10
    assert event.event_team_has_possession is expected


@pytest.mark.parametrize("under_pressure", [True, False, None])
def test_preserves_pressure_value(under_pressure: bool | None) -> None:
    data = context_data()
    data["under_pressure"] = under_pressure

    event = MatchEventContext.model_validate(data)

    assert event.under_pressure is under_pressure


@pytest.mark.parametrize(
    "field",
    ["external_position_id", "position_name"],
)
def test_rejects_empty_position_fields(field: str) -> None:
    data = context_data()
    position = {
        "external_position_id": "19",
        "position_name": "Center Attacking Midfield",
    }
    position[field] = ""
    data["position"] = position

    with pytest.raises(ValidationError):
        MatchEventContext.model_validate(data)


@pytest.mark.parametrize("sequence", [0, -1, "1", True])
def test_rejects_invalid_possession_sequence(sequence: object) -> None:
    data = context_data()
    data["possession_sequence"] = sequence

    with pytest.raises(ValidationError):
        MatchEventContext.model_validate(data)
