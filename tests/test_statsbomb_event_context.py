import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.event_context import (
    normalize_event_context,
)


def source_event(
    event_id: str = "event-1",
    index: int = 1,
) -> dict[str, object]:
    return {
        "id": event_id,
        "index": index,
        "period": 2,
        "timestamp": "00:01:00.000",
        "type": {"name": "Pass"},
        "team": {"id": 779},
        "player": {"id": 5503},
        "position": {"id": 17, "name": "Right Wing"},
        "possession": 50,
        "possession_team": {"id": 779},
        "under_pressure": True,
    }


def test_normalizes_event_context() -> None:
    result = normalize_event_context([source_event()], match_id=3869685)

    assert len(result) == 1
    event = result[0]
    assert event.external_event_id == "event-1"
    assert event.external_match_id == "3869685"
    assert event.external_team_id == "779"
    assert event.external_player_id == "5503"
    assert event.period == 2
    assert event.timestamp_ms == 60_000
    assert event.position is not None
    assert event.position.external_position_id == "17"
    assert event.position.position_name == "Right Wing"
    assert event.possession_sequence == 50
    assert event.external_possession_team_id == "779"
    assert event.under_pressure is True
    assert event.event_team_has_possession is True


def test_preserves_event_without_context_or_player() -> None:
    event = {
        "id": "starting-xi-1",
        "index": 1,
        "period": 1,
        "timestamp": "00:00:00.000",
        "type": {"name": "Starting XI"},
    }

    result = normalize_event_context([event], match_id=3869685)

    assert result[0].external_player_id is None
    assert result[0].position is None
    assert result[0].possession_sequence is None
    assert result[0].external_possession_team_id is None
    assert result[0].under_pressure is None
    assert result[0].event_team_has_possession is None


@pytest.mark.parametrize("under_pressure", [True, False])
def test_preserves_explicit_pressure_value(under_pressure: bool) -> None:
    event = source_event()
    event["under_pressure"] = under_pressure

    result = normalize_event_context([event], match_id=3869685)

    assert result[0].under_pressure is under_pressure


def test_preserves_another_team_as_possession_owner() -> None:
    event = source_event()
    event.update(
        type={"name": "Pressure"},
        possession_team={"id": 771},
    )

    result = normalize_event_context([event], match_id=3869685)

    assert result[0].external_team_id == "779"
    assert result[0].external_possession_team_id == "771"
    assert result[0].event_team_has_possession is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("position", {"id": 0, "name": "Right Wing"}),
        ("position", {"id": 17}),
        ("position", "Right Wing"),
        ("position", {"id": 17, "name": ""}),
        ("possession", 0),
        ("possession", "50"),
        ("possession", True),
        ("possession_team", {"id": 0}),
        ("under_pressure", "true"),
        ("under_pressure", 0),
    ],
)
def test_rejects_invalid_context_fields(field: str, value: object) -> None:
    event = source_event()
    event[field] = value

    with pytest.raises(ProviderUnavailable, match="invalid event context"):
        normalize_event_context([event], match_id=3869685)


def test_orders_events_and_preserves_shootout_period() -> None:
    first = source_event("event-1", 1)
    second = source_event("event-2", 2)
    second["period"] = 5

    result = normalize_event_context([second, first], match_id=3869685)

    assert [event.external_event_id for event in result] == [
        "event-1",
        "event-2",
    ]
    assert result[1].period == 5


def test_rejects_duplicate_event_ids() -> None:
    payload = [
        source_event("event-1", 1),
        source_event("event-1", 2),
    ]

    with pytest.raises(ProviderUnavailable, match="Repeated StatsBomb event ID"):
        normalize_event_context(payload, match_id=3869685)


def test_accepts_empty_event_list() -> None:
    assert normalize_event_context([], match_id=3869685) == []
