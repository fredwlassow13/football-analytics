import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import EventLocation, MatchEventSpatial


def spatial_data() -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "event-1",
        "source_index": 100,
        "period": 1,
        "timestamp_ms": 120_000,
        "event_type": "Shot",
        "external_team_id": "779",
        "external_player_id": "5503",
        "coordinate_system": "statsbomb_120_80",
    }


def test_preserves_missing_locations() -> None:
    event = MatchEventSpatial.model_validate(spatial_data())

    assert event.start_location is None
    assert event.end_location is None
    assert event.coordinate_system == "statsbomb_120_80"


def test_preserves_two_dimensional_destination_without_assuming_zero_height() -> None:
    data = spatial_data()
    data.update(
        start_location={"x": 100.7, "y": 40.1},
        end_location={"x": 110.2, "y": 38.5},
    )

    event = MatchEventSpatial.model_validate(data)

    assert event.start_location is not None
    assert event.start_location.x == 100.7
    assert event.start_location.z is None
    assert event.end_location is not None
    assert event.end_location.x == 110.2
    assert event.end_location.y == 38.5
    assert event.end_location.z is None


def test_preserves_three_dimensional_destination() -> None:
    data = spatial_data()
    data["end_location"] = {"x": 120.0, "y": 40.4, "z": 1.6}

    event = MatchEventSpatial.model_validate(data)

    assert event.end_location is not None
    assert event.end_location.z == 1.6


def test_preserves_explicit_zero_height() -> None:
    location = EventLocation(x=120.0, y=40.0, z=0.0)

    assert location.z == 0.0
    assert location.z is not None


@pytest.mark.parametrize("field", ["x", "y", "z"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_rejects_nonfinite_coordinates(field: str, value: float) -> None:
    data = {"x": 100.0, "y": 40.0, "z": 1.0}
    data[field] = value

    with pytest.raises(ValidationError):
        EventLocation.model_validate(data)


@pytest.mark.parametrize("value", ["100.0", True])
def test_rejects_coordinate_coercion(value: object) -> None:
    with pytest.raises(ValidationError):
        EventLocation.model_validate({"x": value, "y": 40.0})


def test_preserves_values_outside_pitch_rectangle() -> None:
    location = EventLocation(x=121.0, y=-1.0)

    assert location.x == 121.0
    assert location.y == -1.0


def test_requires_coordinate_system() -> None:
    data = spatial_data()
    del data["coordinate_system"]

    with pytest.raises(ValidationError):
        MatchEventSpatial.model_validate(data)


def test_rejects_unsupported_coordinate_system() -> None:
    data = spatial_data()
    data["coordinate_system"] = "metres"

    with pytest.raises(ValidationError):
        MatchEventSpatial.model_validate(data)
