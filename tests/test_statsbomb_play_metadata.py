import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import (
    MatchEventPlayMetadata,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.play_metadata import (
    normalize_event_play_metadata,
)


def source_event(index: int = 1) -> dict[str, object]:
    return {
        "id": f"event-{index}",
        "index": index,
        "period": 1,
        "timestamp": "00:02:00.000",
        "type": {"name": "Pass"},
        "team": {"id": 779, "name": "Argentina"},
        "player": {"id": 5503, "name": "Player"},
    }


def test_preserves_identity_and_source_attributes() -> None:
    event = source_event()
    event["duration"] = 1.25
    event["out"] = True
    event["play_pattern"] = {"id": 1, "name": "Regular Play"}

    result = normalize_event_play_metadata([event], match_id=3869685)[0]

    assert result.provider == "statsbomb"
    assert result.external_match_id == "3869685"
    assert result.external_event_id == "event-1"
    assert result.source_index == 1
    assert result.timestamp_ms == 120_000
    assert result.external_team_id == "779"
    assert result.external_player_id == "5503"
    assert result.duration_seconds == 1.25
    assert result.out is True
    assert result.play_pattern == "regular_play"
    assert result.external_play_pattern_id == "1"


def test_absent_attributes_remain_unknown() -> None:
    result = normalize_event_play_metadata([source_event()], match_id=3869685)[0]

    assert result.duration_seconds is None
    assert result.out is None
    assert result.play_pattern is None
    assert result.external_play_pattern_id is None


def test_preserves_explicit_zero_duration_and_false_out() -> None:
    event = source_event()
    event["duration"] = 0
    event["out"] = False

    result = normalize_event_play_metadata([event], match_id=3869685)[0]

    assert result.duration_seconds == 0.0
    assert result.out is False


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Regular Play", "regular_play"),
        ("From Kick Off", "from_kick_off"),
        ("From Throw In", "from_throw_in"),
        ("From Free Kick", "from_free_kick"),
        ("From Keeper", "from_keeper"),
        ("From Corner", "from_corner"),
        ("From Goal Kick", "from_goal_kick"),
        ("Other", "other"),
        ("From Counter", "from_counter"),
    ],
)
def test_normalizes_pattern_names(
    source: str,
    expected: str,
) -> None:
    event = source_event()
    event["play_pattern"] = {"id": 99, "name": source}

    result = normalize_event_play_metadata([event], match_id=3869685)[0]

    assert result.play_pattern == expected
    assert result.external_play_pattern_id == "99"


@pytest.mark.parametrize(
    "value",
    [None, -1, "1.5", True, float("inf"), float("nan")],
)
def test_rejects_invalid_or_null_duration(value: object) -> None:
    event = source_event()
    event["duration"] = value

    with pytest.raises(ProviderUnavailable, match="event-1"):
        normalize_event_play_metadata([event], match_id=3869685)


@pytest.mark.parametrize("value", [None, "true", 0, 1, []])
def test_rejects_invalid_or_null_out(value: object) -> None:
    event = source_event()
    event["out"] = value

    with pytest.raises(ProviderUnavailable, match="event-1"):
        normalize_event_play_metadata([event], match_id=3869685)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        [],
        {"id": 1},
        {"name": "Regular Play"},
        {"id": 0, "name": "Regular Play"},
        {"id": True, "name": "Regular Play"},
        {"id": 1, "name": ""},
        {"id": 1, "name": "Unknown"},
    ],
)
def test_rejects_invalid_or_unknown_play_pattern(value: object) -> None:
    event = source_event()
    event["play_pattern"] = value

    with pytest.raises(ProviderUnavailable, match="event-1"):
        normalize_event_play_metadata([event], match_id=3869685)


def test_orders_events_and_preserves_shootout() -> None:
    first = source_event(1)
    second = source_event(2)
    second["period"] = 5

    result = normalize_event_play_metadata([second, first], match_id=3869685)

    assert [event.external_event_id for event in result] == ["event-1", "event-2"]
    assert result[1].period == 5


@pytest.mark.parametrize("field", ["id", "index"])
def test_rejects_duplicate_source_headers(field: str) -> None:
    first = source_event(1)
    second = source_event(2)
    second[field] = first[field]

    with pytest.raises(ProviderUnavailable, match="Repeated"):
        normalize_event_play_metadata([first, second], match_id=3869685)


@pytest.mark.parametrize(
    "reference",
    [
        {"play_pattern": "regular_play"},
        {"external_play_pattern_id": "1"},
    ],
)
def test_contract_requires_complete_pattern_reference(
    reference: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="both ID and name"):
        MatchEventPlayMetadata.model_validate(
            {
                "provider": "statsbomb",
                "external_match_id": "3869685",
                "external_event_id": "event-1",
                "source_index": 1,
                "period": 1,
                "timestamp_ms": 0,
                "event_type": "Pass",
            }
            | reference
        )


def test_empty_payload() -> None:
    assert normalize_event_play_metadata([], match_id=3869685) == []


@pytest.mark.parametrize("match_id", [0, -1, True])
def test_rejects_invalid_match_id(match_id: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        normalize_event_play_metadata([], match_id=match_id)
