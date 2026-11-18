import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import (
    EventLocation,
    MatchGoalkeeperAction,
)


def goalkeeper_data() -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "keeper-1",
        "source_index": 100,
        "period": 1,
        "timestamp_ms": 120_000,
        "external_team_id": "779",
        "external_player_id": "keeper-player",
        "goalkeeper_type": "shot_faced",
    }


@pytest.mark.parametrize(
    "goalkeeper_type",
    [
        "collected",
        "goal_conceded",
        "keeper_sweeper",
        "penalty_conceded",
        "penalty_saved",
        "punch",
        "save",
        "shot_faced",
        "shot_saved",
        "smother",
        "penalty_saved_to_post",
        "saved_to_post",
        "shot_saved_off_target",
        "shot_saved_to_post",
    ],
)
def test_preserves_goalkeeper_types(goalkeeper_type: str) -> None:
    result = MatchGoalkeeperAction.model_validate(
        goalkeeper_data() | {"goalkeeper_type": goalkeeper_type}
    )

    assert result.goalkeeper_type == goalkeeper_type


@pytest.mark.parametrize(
    ("goalkeeper_type", "outcome"),
    [
        ("goal_conceded", "no_touch"),
        ("goal_conceded", "touched_in"),
        ("keeper_sweeper", "claim"),
        ("keeper_sweeper", "clear"),
        ("penalty_conceded", "touched_in"),
        ("penalty_saved", "in_play_safe"),
        ("collected", "success"),
        ("punch", "in_play_safe"),
        ("shot_saved", "success"),
        ("shot_saved", "in_play_danger"),
        ("shot_saved", "saved_twice"),
        ("shot_saved", "touched_out"),
        ("save", "success"),
    ],
)
def test_preserves_type_and_outcome(
    goalkeeper_type: str,
    outcome: str,
) -> None:
    result = MatchGoalkeeperAction.model_validate(
        goalkeeper_data() | {"goalkeeper_type": goalkeeper_type, "outcome": outcome}
    )

    assert result.goalkeeper_type == goalkeeper_type
    assert result.outcome == outcome


def test_preserves_identity_and_missing_attributes() -> None:
    result = MatchGoalkeeperAction.model_validate(goalkeeper_data())

    assert result.provider == "statsbomb"
    assert result.external_match_id == "3869685"
    assert result.external_event_id == "keeper-1"
    assert result.external_team_id == "779"
    assert result.external_player_id == "keeper-player"
    assert result.source_index == 100
    assert result.period == 1
    assert result.timestamp_ms == 120_000
    assert result.event_type == "Goal Keeper"
    assert result.outcome is None
    assert result.body_part is None
    assert result.technique is None
    assert result.keeper_position is None
    assert result.start_location is None
    assert result.end_location is None
    assert result.related_event_ids is None


def test_preserves_attributes_locations_and_links() -> None:
    result = MatchGoalkeeperAction.model_validate(
        goalkeeper_data()
        | {
            "goalkeeper_type": "shot_saved",
            "outcome": "in_play_safe",
            "body_part": "left_hand",
            "technique": "diving",
            "keeper_position": "set",
            "start_location": EventLocation(x=119.0, y=40.0),
            "end_location": EventLocation(x=118.0, y=42.0),
            "related_event_ids": ("shot-1", "other-event"),
        }
    )

    assert result.body_part == "left_hand"
    assert result.technique == "diving"
    assert result.keeper_position == "set"
    assert result.coordinate_system == "statsbomb_120_80"
    assert result.start_location is not None
    assert result.start_location.x == 119.0
    assert result.end_location is not None
    assert result.end_location.y == 42.0
    assert result.related_event_ids == ("shot-1", "other-event")


def test_preserves_shootout_period() -> None:
    result = MatchGoalkeeperAction.model_validate(
        goalkeeper_data()
        | {
            "period": 5,
            "goalkeeper_type": "penalty_saved",
            "outcome": "in_play_safe",
        }
    )

    assert result.period == 5


def test_preserves_explicit_empty_related_ids() -> None:
    result = MatchGoalkeeperAction.model_validate(goalkeeper_data() | {"related_event_ids": ()})

    assert result.related_event_ids == ()


@pytest.mark.parametrize("field", ["start_location", "end_location"])
def test_rejects_three_dimensional_goalkeeper_locations(field: str) -> None:
    with pytest.raises(ValidationError, match="two-dimensional"):
        MatchGoalkeeperAction.model_validate(
            goalkeeper_data() | {field: EventLocation(x=119.0, y=40.0, z=1.0)}
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("goalkeeper_type", None),
        ("goalkeeper_type", "unknown"),
        ("goalkeeper_type", "Shot Saved"),
        ("outcome", "unknown"),
        ("body_part", "unknown"),
        ("technique", "unknown"),
        ("keeper_position", "unknown"),
        ("event_type", "Shot"),
        ("related_event_ids", ("",)),
        ("related_event_ids", ("shot-1", 123)),
        ("coordinate_system", "other"),
    ],
)
def test_rejects_invalid_contract_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        MatchGoalkeeperAction.model_validate(goalkeeper_data() | {field: value})


def test_requires_goalkeeper_type() -> None:
    data = goalkeeper_data()
    del data["goalkeeper_type"]

    with pytest.raises(ValidationError):
        MatchGoalkeeperAction.model_validate(data)


def test_rejects_unexpected_fields() -> None:
    with pytest.raises(ValidationError):
        MatchGoalkeeperAction.model_validate(goalkeeper_data() | {"inferred_save": True})
