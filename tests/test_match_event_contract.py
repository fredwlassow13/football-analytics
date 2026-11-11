import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import MatchEvent


def event_data() -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "synthetic-event-1",
        "source_index": 1,
        "period": 1,
        "timestamp_ms": 0,
        "event_type": "Starting XI",
    }


def test_allows_event_without_team_or_player() -> None:
    event = MatchEvent.model_validate(event_data())

    assert event.external_team_id is None
    assert event.external_player_id is None


def test_preserves_period_local_time_and_external_references() -> None:
    data = event_data()
    data.update(
        period=2,
        timestamp_ms=60_000,
        external_team_id="team-1",
        external_player_id="player-1",
    )

    event = MatchEvent.model_validate(data)

    assert event.period == 2
    assert event.timestamp_ms == 60_000
    assert event.external_team_id == "team-1"
    assert event.external_player_id == "player-1"


def test_accepts_penalty_shootout_period() -> None:
    data = event_data()
    data["period"] = 5

    event = MatchEvent.model_validate(data)

    assert event.period == 5


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", ""),
        ("external_match_id", ""),
        ("external_event_id", ""),
        ("source_index", 0),
        ("source_index", True),
        ("period", 0),
        ("timestamp_ms", -1),
        ("timestamp_ms", "1000"),
        ("event_type", ""),
        ("external_team_id", ""),
        ("external_player_id", ""),
    ],
)
def test_rejects_invalid_event_fields(field: str, value: object) -> None:
    data = event_data()
    data[field] = value

    with pytest.raises(ValidationError):
        MatchEvent.model_validate(data)


def test_rejects_unexpected_fields() -> None:
    data = event_data()
    data["elapsed_ms"] = 60_000

    with pytest.raises(ValidationError):
        MatchEvent.model_validate(data)
