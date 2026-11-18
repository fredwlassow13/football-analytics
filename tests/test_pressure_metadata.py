import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.event_context import (
    normalize_event_context,
)
from football_analytics.integrations.statsbomb.pressure_metadata import (
    normalize_event_pressure_metadata,
)


def source_event(
    index: int = 1,
    *,
    event_type: str = "Pass",
    period: int = 1,
) -> dict[str, object]:
    return {
        "id": f"event-{index}",
        "index": index,
        "period": period,
        "timestamp": "00:01:00.000",
        "type": {"name": event_type},
        "team": {"id": 779},
        "player": {"id": 5503},
    }


@pytest.mark.parametrize(
    "event_type",
    [
        "Pass",
        "Interception",
        "Duel",
        "Pressure",
        "Miscontrol",
        "Shot",
        "Half End",
    ],
)
def test_normalizes_flags_for_any_event_type(event_type: str) -> None:
    event = source_event(event_type=event_type)
    event["under_pressure"] = True
    event["counterpress"] = False

    result = normalize_event_pressure_metadata([event], match_id=3869685)

    assert len(result) == 1
    metadata = result[0]
    assert metadata.provider == "statsbomb"
    assert metadata.external_match_id == "3869685"
    assert metadata.external_event_id == "event-1"
    assert metadata.source_index == 1
    assert metadata.period == 1
    assert metadata.timestamp_ms == 60_000
    assert metadata.event_type == event_type
    assert metadata.external_team_id == "779"
    assert metadata.external_player_id == "5503"
    assert metadata.under_pressure is True
    assert metadata.counterpress is False


def test_absent_flags_remain_unknown() -> None:
    result = normalize_event_pressure_metadata(
        [source_event()],
        match_id=3869685,
    )
    assert result[0].under_pressure is None
    assert result[0].counterpress is None


@pytest.mark.parametrize("field", ["under_pressure", "counterpress"])
@pytest.mark.parametrize("value", [True, False])
def test_preserves_independent_boolean_flags(field: str, value: bool) -> None:
    event = source_event()
    event[field] = value

    metadata = normalize_event_pressure_metadata(
        [event],
        match_id=3869685,
    )[0]

    assert getattr(metadata, field) is value
    other = "counterpress" if field == "under_pressure" else "under_pressure"
    assert getattr(metadata, other) is None


@pytest.mark.parametrize("field", ["under_pressure", "counterpress"])
@pytest.mark.parametrize("value", [None, "true", 1, 0, [], {}])
def test_rejects_null_and_non_boolean_flags(field: str, value: object) -> None:
    event = source_event()
    event[field] = value

    with pytest.raises(ProviderUnavailable):
        normalize_event_pressure_metadata([event], match_id=3869685)


def test_under_pressure_matches_existing_context() -> None:
    first = source_event(1)
    second = source_event(2, event_type="Pressure")
    third = source_event(3, event_type="Duel")
    first["under_pressure"] = True
    third["under_pressure"] = False
    payload = [third, first, second]

    contexts = normalize_event_context(payload, match_id=3869685)
    metadata = normalize_event_pressure_metadata(payload, match_id=3869685)

    assert [(event.external_event_id, event.under_pressure) for event in metadata] == [
        (event.external_event_id, event.under_pressure) for event in contexts
    ]


def test_preserves_source_order_and_shootout_period() -> None:
    result = normalize_event_pressure_metadata(
        [
            source_event(3, period=5),
            source_event(1),
            source_event(2, period=2),
        ],
        match_id=3869685,
    )

    assert [event.external_event_id for event in result] == ["event-1", "event-2", "event-3"]
    assert [event.period for event in result] == [1, 2, 5]


def test_preserves_missing_actor_references() -> None:
    event = source_event()
    del event["team"]
    del event["player"]

    metadata = normalize_event_pressure_metadata(
        [event],
        match_id=3869685,
    )[0]

    assert metadata.external_team_id is None
    assert metadata.external_player_id is None


def test_rejects_duplicate_source_events() -> None:
    event = source_event()

    with pytest.raises(ProviderUnavailable):
        normalize_event_pressure_metadata([event, event], match_id=3869685)
