import pytest

from football_analytics.ingestion.movement_geometry import (
    MovementDirection,
    MovementGeometryIssue,
    analyze_movement_geometry,
)
from football_analytics.integrations.base import EventLocation, MatchEventSpatial


def movement(
    start: tuple[float, float] = (70.0, 40.0),
    end: tuple[float, float] = (90.0, 40.0),
    *,
    event_type: str = "Pass",
    period: int = 1,
    provider: str = "statsbomb",
) -> MatchEventSpatial:
    return MatchEventSpatial(
        provider=provider,
        external_match_id="3869685",
        external_event_id="movement-1",
        source_index=1,
        period=period,
        timestamp_ms=1000,
        event_type=event_type,
        external_team_id="779",
        external_player_id="5503",
        coordinate_system="statsbomb_120_80",
        start_location=EventLocation(x=start[0], y=start[1]),
        end_location=EventLocation(x=end[0], y=end[1]),
    )


@pytest.mark.parametrize("event_type", ["Pass", "Carry"])
def test_calculates_geometry_and_preserves_source(event_type: str) -> None:
    event = movement(
        (70.0, 40.0),
        (90.0, 40.0),
        event_type=event_type,
    )
    original = event.model_dump()

    result = analyze_movement_geometry(event)

    assert result.issues == ()
    assert result.delta_x == 20.0
    assert result.delta_y == 0.0
    assert result.straight_line_distance == 20.0
    assert result.goal_distance_reduction == 20.0
    assert result.direction == "forward"
    assert result.enters_final_third is True
    assert result.enters_penalty_area is False
    assert result.source.model_dump() == original
    assert event.model_dump() == original


@pytest.mark.parametrize(
    ("end", "direction"),
    [
        ((90.0, 40.0), "forward"),
        ((50.0, 40.0), "backward"),
        ((70.0, 50.0), "lateral"),
        ((70.0, 40.0), "stationary"),
    ],
)
def test_direction(
    end: tuple[float, float],
    direction: MovementDirection,
) -> None:
    assert analyze_movement_geometry(movement(end=end)).direction == direction


@pytest.mark.parametrize(
    ("start_x", "end_x", "expected"),
    [
        (79.9, 80.0, True),
        (80.0, 90.0, False),
        (90.0, 79.9, False),
        (80.0, 80.0, False),
    ],
)
def test_final_third_boundary(
    start_x: float,
    end_x: float,
    expected: bool,
) -> None:
    result = analyze_movement_geometry(movement((start_x, 40.0), (end_x, 40.0)))
    assert result.enters_final_third is expected


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ((101.9, 40.0), (102.0, 40.0), True),
        ((110.0, 17.9), (110.0, 18.0), True),
        ((110.0, 62.1), (110.0, 62.0), True),
        ((102.0, 40.0), (110.0, 40.0), False),
        ((100.0, 10.0), (110.0, 17.9), False),
        ((110.0, 40.0), (100.0, 40.0), False),
        ((100.0, 40.0), (120.0, 40.0), True),
    ],
)
def test_penalty_area_boundaries(
    start: tuple[float, float],
    end: tuple[float, float],
    expected: bool,
) -> None:
    result = analyze_movement_geometry(movement(start, end))
    assert result.enters_penalty_area is expected


def test_forward_direction_does_not_guarantee_goal_distance_reduction() -> None:
    result = analyze_movement_geometry(movement((110.0, 40.0), (111.0, 80.0)))

    assert result.direction == "forward"
    assert result.goal_distance_reduction is not None
    assert result.goal_distance_reduction < 0


def test_distance_is_the_segment_between_endpoints() -> None:
    result = analyze_movement_geometry(movement((10.0, 10.0), (13.0, 14.0), event_type="Carry"))
    assert result.straight_line_distance == 5.0


@pytest.mark.parametrize(
    ("field", "point", "issue"),
    [
        ("start_location", None, "missing_start_location"),
        ("end_location", None, "missing_end_location"),
        (
            "start_location",
            EventLocation(x=10.0, y=40.0, z=0.0),
            "start_location_not_2d",
        ),
        (
            "end_location",
            EventLocation(x=90.0, y=40.0, z=1.0),
            "end_location_not_2d",
        ),
        (
            "start_location",
            EventLocation(x=-0.1, y=40.0),
            "start_location_outside_pitch",
        ),
        (
            "end_location",
            EventLocation(x=120.1, y=40.0),
            "end_location_outside_pitch",
        ),
        (
            "end_location",
            EventLocation(x=90.0, y=80.1),
            "end_location_outside_pitch",
        ),
    ],
)
def test_invalid_geometry_is_unavailable_without_clipping(
    field: str,
    point: EventLocation | None,
    issue: MovementGeometryIssue,
) -> None:
    data = movement().model_dump()
    data[field] = None if point is None else point.model_dump()
    event = MatchEventSpatial.model_validate(data)

    result = analyze_movement_geometry(event)

    assert result.issues == (issue,)
    assert result.source.model_dump() == event.model_dump()
    assert result.delta_x is None
    assert result.straight_line_distance is None
    assert result.goal_distance_reduction is None
    assert result.direction is None
    assert result.enters_final_third is None
    assert result.enters_penalty_area is None


def test_preserves_multiple_diagnostics() -> None:
    event = movement()
    data = event.model_dump()
    data["start_location"] = None
    data["end_location"] = None

    result = analyze_movement_geometry(MatchEventSpatial.model_validate(data))

    assert result.issues == (
        "missing_start_location",
        "missing_end_location",
    )


@pytest.mark.parametrize("period", [1, 2, 3, 4])
def test_orientation_is_the_same_in_each_playing_period(period: int) -> None:
    result = analyze_movement_geometry(movement(period=period))
    assert result.delta_x == 20.0
    assert result.direction == "forward"


def test_rejects_shootout() -> None:
    with pytest.raises(ValueError, match="playing period"):
        analyze_movement_geometry(movement(period=5))


def test_rejects_other_action_types() -> None:
    with pytest.raises(ValueError, match="Pass and Carry"):
        analyze_movement_geometry(movement(event_type="Shot"))


def test_rejects_provider_without_supported_orientation() -> None:
    with pytest.raises(ValueError, match="StatsBomb orientation"):
        analyze_movement_geometry(movement(provider="other"))
