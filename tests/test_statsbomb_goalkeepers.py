import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.goalkeepers import (
    normalize_goalkeeper_actions,
)


def source_event(
    index: int = 1,
    *,
    goalkeeper_type: str = "Shot Faced",
) -> dict[str, object]:
    return {
        "id": f"keeper-{index}",
        "index": index,
        "period": 1,
        "timestamp": "00:02:00.000",
        "type": {"name": "Goal Keeper"},
        "team": {"id": 779, "name": "Argentina"},
        "player": {"id": 6909, "name": "Goalkeeper"},
        "goalkeeper": {"type": {"name": goalkeeper_type}},
    }


def test_preserves_source_identity_and_all_attributes() -> None:
    event = source_event(goalkeeper_type="Shot Saved")
    event["location"] = [119.0, 40.0]
    event["related_events"] = ["shot-1", "other-event"]
    event["goalkeeper"] = {
        "type": {"name": "Shot Saved"},
        "outcome": {"name": "In Play Safe"},
        "body_part": {"name": "Left Hand"},
        "technique": {"name": "Diving"},
        "position": {"name": "Set"},
        "end_location": [118.0, 42.0],
    }

    result = normalize_goalkeeper_actions([event], match_id=3869685)[0]

    assert result.provider == "statsbomb"
    assert result.external_match_id == "3869685"
    assert result.external_event_id == "keeper-1"
    assert result.external_team_id == "779"
    assert result.external_player_id == "6909"
    assert result.source_index == 1
    assert result.timestamp_ms == 120_000
    assert result.goalkeeper_type == "shot_saved"
    assert result.outcome == "in_play_safe"
    assert result.body_part == "left_hand"
    assert result.technique == "diving"
    assert result.keeper_position == "set"
    assert result.coordinate_system == "statsbomb_120_80"
    assert result.start_location is not None
    assert result.start_location.x == 119.0
    assert result.end_location is not None
    assert result.end_location.y == 42.0
    assert result.related_event_ids == ("shot-1", "other-event")


def test_preserves_absent_optional_attributes() -> None:
    result = normalize_goalkeeper_actions([source_event()], match_id=3869685)[0]

    assert result.goalkeeper_type == "shot_faced"
    assert result.outcome is None
    assert result.body_part is None
    assert result.technique is None
    assert result.keeper_position is None
    assert result.start_location is None
    assert result.end_location is None
    assert result.related_event_ids is None


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Collected", "collected"),
        ("Goal Conceded", "goal_conceded"),
        ("Keeper Sweeper", "keeper_sweeper"),
        ("Penalty Conceded", "penalty_conceded"),
        ("Penalty Saved", "penalty_saved"),
        ("Punch", "punch"),
        ("Save", "save"),
        ("Shot Faced", "shot_faced"),
        ("Shot Saved", "shot_saved"),
        ("Smother", "smother"),
        ("Penalty Saved To Post", "penalty_saved_to_post"),
        ("Saved To Post", "saved_to_post"),
        ("Shot Saved Off T", "shot_saved_off_target"),
        ("Shot Saved Off Target", "shot_saved_off_target"),
        ("Shot Saved To Post", "shot_saved_to_post"),
    ],
)
def test_normalizes_goalkeeper_type_names(
    source: str,
    expected: str,
) -> None:
    result = normalize_goalkeeper_actions(
        [source_event(goalkeeper_type=source)],
        match_id=3869685,
    )[0]

    assert result.goalkeeper_type == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Success", "success"),
        ("In Play Danger", "in_play_danger"),
        ("In Play Safe", "in_play_safe"),
        ("Saved Twice", "saved_twice"),
        ("Touched Out", "touched_out"),
    ],
)
def test_preserves_distinct_shot_saved_outcomes(
    source: str,
    expected: str,
) -> None:
    event = source_event()
    event["goalkeeper"] = {
        "type": {"name": "Shot Saved"},
        "outcome": {"name": source},
    }

    result = normalize_goalkeeper_actions([event], match_id=3869685)[0]

    assert result.outcome == expected


@pytest.mark.parametrize(
    "field",
    ["outcome", "body_part", "technique", "position", "end_location"],
)
def test_rejects_explicit_null_detail_fields(field: str) -> None:
    event = source_event()
    event["goalkeeper"] = {
        "type": {"name": "Shot Faced"},
        field: None,
    }

    with pytest.raises(ProviderUnavailable, match="keeper-1"):
        normalize_goalkeeper_actions([event], match_id=3869685)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", {"name": "Unknown"}),
        ("outcome", {"name": "Unknown"}),
        ("body_part", {"name": "Unknown"}),
        ("technique", {"name": "Unknown"}),
        ("position", {"name": "Unknown"}),
        ("outcome", {"name": ""}),
        ("type", None),
    ],
)
def test_rejects_unknown_or_invalid_detail_values(
    field: str,
    value: object,
) -> None:
    event = source_event()
    event["goalkeeper"] = {
        "type": {"name": "Shot Faced"},
        field: value,
    }

    with pytest.raises(ProviderUnavailable, match="keeper-1"):
        normalize_goalkeeper_actions([event], match_id=3869685)


@pytest.mark.parametrize("details", [None, {}, [], "invalid"])
def test_rejects_invalid_goalkeeper_object(details: object) -> None:
    event = source_event()
    event["goalkeeper"] = details

    with pytest.raises(ProviderUnavailable):
        normalize_goalkeeper_actions([event], match_id=3869685)


def test_requires_goalkeeper_object() -> None:
    event = source_event()
    del event["goalkeeper"]

    with pytest.raises(ProviderUnavailable):
        normalize_goalkeeper_actions([event], match_id=3869685)


@pytest.mark.parametrize("field", ["location", "end_location"])
@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        [1.0],
        [1.0, 2.0, 3.0],
        ["1", 2.0],
        [True, 2.0],
        [float("inf"), 2.0],
    ],
)
def test_rejects_invalid_coordinates(
    field: str,
    value: object,
) -> None:
    event = source_event()

    if field == "location":
        event["location"] = value
    else:
        event["goalkeeper"] = {
            "type": {"name": "Shot Faced"},
            "end_location": value,
        }

    with pytest.raises(ProviderUnavailable):
        normalize_goalkeeper_actions([event], match_id=3869685)


def test_preserves_coordinates_without_clamping() -> None:
    event = source_event()
    event["location"] = [121.0, -1.0]

    result = normalize_goalkeeper_actions([event], match_id=3869685)[0]

    assert result.start_location is not None
    assert result.start_location.x == 121.0
    assert result.start_location.y == -1.0


@pytest.mark.parametrize("value", [None, "shot-1", [1], [""]])
def test_rejects_invalid_related_event_ids(value: object) -> None:
    event = source_event()
    event["related_events"] = value

    with pytest.raises(ProviderUnavailable):
        normalize_goalkeeper_actions([event], match_id=3869685)


def test_preserves_empty_related_list() -> None:
    event = source_event()
    event["related_events"] = []

    result = normalize_goalkeeper_actions([event], match_id=3869685)[0]

    assert result.related_event_ids == ()


def test_filters_orders_and_preserves_shootout() -> None:
    first = source_event(1)
    second = source_event(2, goalkeeper_type="Penalty Saved")
    second["period"] = 5

    unrelated = {
        "id": "pass-1",
        "index": 3,
        "period": 1,
        "timestamp": "00:03:00.000",
        "type": {"name": "Pass"},
    }

    result = normalize_goalkeeper_actions(
        [second, unrelated, first],
        match_id=3869685,
    )

    assert [event.external_event_id for event in result] == ["keeper-1", "keeper-2"]
    assert result[1].period == 5


@pytest.mark.parametrize("field", ["id", "index"])
def test_rejects_duplicate_source_headers(field: str) -> None:
    first = source_event(1)
    second = source_event(2)
    second[field] = first[field]

    with pytest.raises(ProviderUnavailable, match="Repeated"):
        normalize_goalkeeper_actions([first, second], match_id=3869685)


def test_empty_payload() -> None:
    assert normalize_goalkeeper_actions([], match_id=3869685) == []


@pytest.mark.parametrize("match_id", [0, -1, True])
def test_rejects_invalid_match_id(match_id: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        normalize_goalkeeper_actions([], match_id=match_id)
