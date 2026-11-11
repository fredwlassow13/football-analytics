import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.spatial import (
    normalize_event_spatial,
)


def source_event(
    event_id: str = "event-1",
    index: int = 1,
) -> dict[str, object]:
    return {
        "id": event_id,
        "index": index,
        "period": 1,
        "timestamp": "00:01:00.000",
        "type": {"name": "Dribble"},
        "team": {"id": 779},
        "player": {"id": 5503},
        "location": [100.7, 40.1],
    }


def test_normalizes_start_location_without_inventing_destination() -> None:
    result = normalize_event_spatial([source_event()], match_id=3869685)

    assert len(result) == 1
    event = result[0]
    assert event.external_event_id == "event-1"
    assert event.timestamp_ms == 60_000
    assert event.coordinate_system == "statsbomb_120_80"
    assert event.start_location is not None
    assert event.start_location.model_dump() == {
        "x": 100.7,
        "y": 40.1,
        "z": None,
    }
    assert event.end_location is None


@pytest.mark.parametrize(
    ("event_type", "detail_key"),
    [("Pass", "pass"), ("Carry", "carry")],
)
def test_normalizes_two_dimensional_destination(
    event_type: str,
    detail_key: str,
) -> None:
    event = source_event()
    event["type"] = {"name": event_type}
    event[detail_key] = {"end_location": [110.2, 38.5]}

    result = normalize_event_spatial([event], match_id=3869685)

    destination = result[0].end_location
    assert destination is not None
    assert destination.x == 110.2
    assert destination.y == 38.5
    assert destination.z is None


@pytest.mark.parametrize(
    ("coordinates", "expected_z"),
    [
        ([120.0, 40.4], None),
        ([120.0, 40.4, 1.6], 1.6),
    ],
)
def test_preserves_shot_destination_dimensions(
    coordinates: list[float],
    expected_z: float | None,
) -> None:
    event = source_event()
    event["type"] = {"name": "Shot"}
    event["shot"] = {"end_location": coordinates}

    result = normalize_event_spatial([event], match_id=3869685)

    destination = result[0].end_location
    assert destination is not None
    assert destination.z == expected_z


def test_preserves_event_without_spatial_data() -> None:
    event = source_event()
    event["type"] = {"name": "Starting XI"}
    del event["location"]

    result = normalize_event_spatial([event], match_id=3869685)

    assert result[0].start_location is None
    assert result[0].end_location is None


@pytest.mark.parametrize(
    "coordinates",
    [
        [],
        [1],
        [1, 2, 3],
        [1, 2, 3, 4],
        ["1", 2],
        [True, 2],
        [float("inf"), 2],
    ],
)
def test_rejects_invalid_start_location(coordinates: object) -> None:
    event = source_event()
    event["location"] = coordinates

    with pytest.raises(ProviderUnavailable, match="Invalid spatial details"):
        normalize_event_spatial([event], match_id=3869685)


@pytest.mark.parametrize(
    ("event_type", "detail_key", "coordinates"),
    [
        ("Pass", "pass", [100, 40, 1]),
        ("Carry", "carry", [100, 40, 1]),
        ("Shot", "shot", [100, 40, 1, 2]),
        ("Shot", "shot", [100]),
        ("Shot", "shot", [100, 40, float("nan")]),
        ("Shot", "shot", [100, 40, True]),
    ],
)
def test_rejects_invalid_destination(
    event_type: str,
    detail_key: str,
    coordinates: object,
) -> None:
    event = source_event()
    event["type"] = {"name": event_type}
    event[detail_key] = {"end_location": coordinates}

    with pytest.raises(ProviderUnavailable, match="Invalid spatial details"):
        normalize_event_spatial([event], match_id=3869685)


def test_rejects_action_without_start_location() -> None:
    event = source_event()
    del event["location"]

    with pytest.raises(ProviderUnavailable, match="Invalid spatial details"):
        normalize_event_spatial([event], match_id=3869685)


def test_rejects_pass_without_destination() -> None:
    event = source_event()
    event["type"] = {"name": "Pass"}
    event["pass"] = {}

    with pytest.raises(ProviderUnavailable, match="Invalid spatial details"):
        normalize_event_spatial([event], match_id=3869685)


def test_orders_events_and_preserves_shootout_period() -> None:
    first = source_event("event-1", 1)
    second = source_event("event-2", 2)
    second.update(
        period=5,
        type={"name": "Shot"},
        shot={"end_location": [120.0, 40.4, 1.6]},
    )

    result = normalize_event_spatial([second, first], match_id=3869685)

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
        normalize_event_spatial(payload, match_id=3869685)


def test_accepts_empty_event_list() -> None:
    assert normalize_event_spatial([], match_id=3869685) == []
