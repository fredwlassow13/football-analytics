import pytest

from football_analytics.ingestion.ball_control_evidence import (
    classify_ball_control_evidence,
)
from football_analytics.integrations.base import (
    MatchBallControlIncident,
    MatchBallRecovery,
    MatchBlock,
    MatchClearance,
    MatchDribble,
    MatchDribbledPast,
    MatchDuel,
    MatchEvent,
    MatchEventSpatial,
    MatchInterception,
    MatchPass,
    MatchPressure,
)

MODELS = {
    "Dispossessed": MatchBallControlIncident,
    "Miscontrol": MatchBallControlIncident,
    "Ball Recovery": MatchBallRecovery,
    "Block": MatchBlock,
    "Clearance": MatchClearance,
    "Dribble": MatchDribble,
    "Dribbled Past": MatchDribbledPast,
    "Duel": MatchDuel,
    "Interception": MatchInterception,
    "Pass": MatchPass,
    "Pressure": MatchPressure,
}


def source_action(event_type="Miscontrol", **attributes):
    return MODELS[event_type].model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_event_id": "event-1",
            "source_index": 1,
            "period": 1,
            "timestamp_ms": 1000,
            "event_type": event_type,
            "external_team_id": "779",
            "external_player_id": "5503",
        }
        | attributes
    )


def spatial_for(action, point=(50.0, 40.0)):
    header = {field: getattr(action, field) for field in MatchEvent.model_fields}
    return MatchEventSpatial.model_validate(
        header
        | {
            "coordinate_system": "statsbomb_120_80",
            "start_location": (
                None
                if point is None
                else {
                    "x": point[0],
                    "y": point[1],
                    "z": point[2] if len(point) == 3 else None,
                }
            ),
        }
    )


@pytest.mark.parametrize(
    ("event_type", "attributes", "category", "outcome"),
    [
        ("Dispossessed", {}, "dispossessed", None),
        ("Miscontrol", {}, "miscontrol", None),
        ("Pass", {"outcome": "complete"}, "completed_pass", "complete"),
        ("Pass", {"outcome": "incomplete"}, "non_completed_pass", "incomplete"),
        ("Pass", {"outcome": "out"}, "non_completed_pass", "out"),
        ("Pass", {"outcome": "offside"}, "non_completed_pass", "offside"),
        ("Pass", {"outcome": "unknown"}, "unknown_pass_outcome", "unknown"),
        ("Dribble", {"outcome": "complete"}, "completed_dribble", "complete"),
        (
            "Dribble",
            {"outcome": "incomplete"},
            "non_completed_dribble",
            "incomplete",
        ),
        ("Ball Recovery", {}, "recovery_failure_unknown", None),
        (
            "Ball Recovery",
            {"recovery_failure": True},
            "recovery_failure_true",
            None,
        ),
        (
            "Ball Recovery",
            {"recovery_failure": False},
            "recovery_failure_false",
            None,
        ),
        (
            "Interception",
            {"outcome": "lost_out"},
            "interception",
            "lost_out",
        ),
        (
            "Duel",
            {"duel_type": "tackle", "outcome": "success_in_play"},
            "tackle",
            "success_in_play",
        ),
        ("Duel", {"duel_type": "aerial_lost"}, "aerial_lost", None),
        ("Pressure", {}, "pressure", None),
        ("Dribbled Past", {}, "dribbled_past", None),
        ("Block", {}, "block", None),
        ("Clearance", {}, "clearance", None),
    ],
)
def test_preserves_source_evidence(event_type, attributes, category, outcome):
    action = source_action(event_type, **attributes)
    spatial = spatial_for(action)

    result = classify_ball_control_evidence(action, spatial)

    assert result.category == category
    assert result.reported_outcome == outcome
    assert result.source.model_dump() == action.model_dump()
    assert result.spatial.model_dump() == spatial.model_dump()
    assert result.unit_of_analysis == "source_event"
    assert result.zone_issues == ()


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        (0.0, "defensive_third"),
        (39.9, "defensive_third"),
        (40.0, "middle_third"),
        (79.9, "middle_third"),
        (80.0, "attacking_third"),
        (120.0, "attacking_third"),
    ],
)
def test_longitudinal_boundaries(x, expected):
    action = source_action()
    result = classify_ball_control_evidence(action, spatial_for(action, (x, 40.0)))
    assert result.longitudinal_zone == expected


@pytest.mark.parametrize(
    ("y", "expected"),
    [
        (0.0, "low_y"),
        (19.9, "low_y"),
        (20.0, "central"),
        (59.9, "central"),
        (60.0, "high_y"),
        (80.0, "high_y"),
    ],
)
def test_lateral_boundaries(y, expected):
    action = source_action()
    result = classify_ball_control_evidence(action, spatial_for(action, (50.0, y)))
    assert result.lateral_zone == expected


@pytest.mark.parametrize(
    ("point", "issues"),
    [
        (None, ("missing_start_location",)),
        ((50.0, 40.0, 0.0), ("start_location_not_2d",)),
        ((120.1, 40.0), ("start_location_outside_pitch",)),
        ((50.0, -0.1), ("start_location_outside_pitch",)),
        (
            (-1.0, 40.0, 1.0),
            ("start_location_not_2d", "start_location_outside_pitch"),
        ),
    ],
)
def test_unavailable_zone_preserves_category_and_coordinates(point, issues):
    action = source_action()
    spatial = spatial_for(action, point)
    result = classify_ball_control_evidence(action, spatial)

    assert result.category == "miscontrol"
    assert result.longitudinal_zone is None
    assert result.lateral_zone is None
    assert result.zone_issues == issues
    assert result.spatial.model_dump() == spatial.model_dump()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "other"),
        ("external_match_id", "another-match"),
        ("external_event_id", "another-event"),
        ("source_index", 2),
        ("period", 2),
        ("timestamp_ms", 2000),
        ("event_type", "Dispossessed"),
        ("external_team_id", "771"),
        ("external_player_id", "another-player"),
    ],
)
def test_rejects_spatial_header_mismatch(field, value):
    action = source_action()
    data = spatial_for(action).model_dump()
    data[field] = value

    with pytest.raises(ValueError, match="Spatial header mismatch"):
        classify_ball_control_evidence(action, MatchEventSpatial.model_validate(data))


def test_rejects_shootout():
    action = source_action(period=5)
    with pytest.raises(ValueError, match="playing period"):
        classify_ball_control_evidence(action, spatial_for(action))


def test_rejects_unverified_provider_orientation():
    action = source_action(provider="other")
    with pytest.raises(ValueError, match="StatsBomb orientation"):
        classify_ball_control_evidence(action, spatial_for(action))
