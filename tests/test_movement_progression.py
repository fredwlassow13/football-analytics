import pytest

from football_analytics.ingestion.movement_progression import (
    classify_movement_progression,
)
from football_analytics.integrations.base import (
    EventLocation,
    MatchEvent,
    MatchEventSpatial,
    MatchPass,
)


def movement(
    start: tuple[float, float] = (80.0, 40.0),
    end: tuple[float, float] = (90.0, 40.0),
    *,
    event_type: str = "Pass",
) -> MatchEventSpatial:
    return MatchEventSpatial(
        provider="statsbomb",
        external_match_id="3869685",
        external_event_id="movement-1",
        source_index=1,
        period=1,
        timestamp_ms=1000,
        event_type=event_type,
        external_team_id="779",
        external_player_id="5503",
        coordinate_system="statsbomb_120_80",
        start_location=EventLocation(x=start[0], y=start[1]),
        end_location=EventLocation(x=end[0], y=end[1]),
    )


def normalized_pass(
    event: MatchEventSpatial,
    outcome: str = "complete",
) -> MatchPass:
    header = {field: getattr(event, field) for field in MatchEvent.model_fields}
    return MatchPass.model_validate(header | {"outcome": outcome})


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ((80.0, 40.0), (90.0, 40.0), True),
        ((80.0, 40.0), (89.9, 40.0), False),
        ((20.0, 40.0), (44.9, 40.0), False),
        ((20.0, 40.0), (45.0, 40.0), True),
        ((70.0, 40.0), (80.0, 40.0), False),
        ((70.0, 40.0), (70.0, 60.0), False),
        ((90.0, 40.0), (70.0, 40.0), False),
        ((110.0, 40.0), (111.0, 80.0), False),
        ((120.0, 40.0), (120.0, 40.0), False),
    ],
)
def test_progression_requires_both_geometric_thresholds(
    start: tuple[float, float],
    end: tuple[float, float],
    expected: bool,
) -> None:
    event = movement(start, end)
    result = classify_movement_progression(
        event,
        pass_event=normalized_pass(event),
    )

    assert result.geometrically_progressive is expected
    assert result.countable_progression is expected


@pytest.mark.parametrize(
    ("outcome", "eligible"),
    [
        ("complete", True),
        ("incomplete", False),
        ("out", False),
        ("offside", False),
        ("unknown", None),
    ],
)
def test_pass_outcome_controls_counts_without_changing_geometry(
    outcome: str,
    eligible: bool | None,
) -> None:
    event = movement((70.0, 40.0), (110.0, 40.0))
    pass_event = normalized_pass(event, outcome)

    result = classify_movement_progression(event, pass_event=pass_event)

    assert result.geometrically_progressive is True
    assert result.geometry.enters_final_third is True
    assert result.geometry.enters_penalty_area is True
    assert result.eligible_for_count is eligible
    assert result.countable_progression is eligible
    assert result.countable_final_third_entry is eligible
    assert result.countable_penalty_area_entry is eligible
    assert result.pass_event == pass_event


def test_entry_does_not_require_progression() -> None:
    event = movement((79.0, 40.0), (80.0, 40.0))

    result = classify_movement_progression(
        event,
        pass_event=normalized_pass(event),
    )

    assert result.geometrically_progressive is False
    assert result.countable_progression is False
    assert result.countable_final_third_entry is True


def test_carry_counts_recorded_movement_without_pass_outcome() -> None:
    event = movement(
        (70.0, 40.0),
        (110.0, 40.0),
        event_type="Carry",
    )

    result = classify_movement_progression(event)

    assert result.pass_event is None
    assert result.eligible_for_count is True
    assert result.countable_progression is True
    assert result.countable_final_third_entry is True
    assert result.countable_penalty_area_entry is True


def test_invalid_geometry_preserves_known_pass_eligibility() -> None:
    event = movement((70.0, 40.0), (120.1, 40.0))

    result = classify_movement_progression(
        event,
        pass_event=normalized_pass(event),
    )

    assert result.eligible_for_count is True
    assert result.geometry.issues == ("end_location_outside_pitch",)
    assert result.geometrically_progressive is None
    assert result.countable_progression is None
    assert result.countable_final_third_entry is None
    assert result.countable_penalty_area_entry is None


def test_pass_requires_its_normalized_outcome() -> None:
    with pytest.raises(ValueError, match="requires its normalized pass"):
        classify_movement_progression(movement())


def test_carry_rejects_pass_details() -> None:
    pass_event = normalized_pass(movement())
    carry = movement(event_type="Carry")

    with pytest.raises(ValueError, match="Carry must not receive"):
        classify_movement_progression(carry, pass_event=pass_event)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "other"),
        ("external_match_id", "another-match"),
        ("external_event_id", "another-event"),
        ("source_index", 2),
        ("period", 2),
        ("timestamp_ms", 2000),
        ("external_team_id", "771"),
        ("external_player_id", "another-player"),
    ],
)
def test_rejects_pass_header_mismatch(field: str, value: object) -> None:
    event = movement()
    data = normalized_pass(event).model_dump()
    data[field] = value
    pass_event = MatchPass.model_validate(data)

    with pytest.raises(ValueError, match=f"Pass header mismatch: {field}"):
        classify_movement_progression(event, pass_event=pass_event)
