import pytest

from football_analytics.ingestion.action_context import contextualize_actions
from football_analytics.integrations.base import (
    MatchEventContext,
    MatchEventSpatial,
    MatchPass,
)


def make_inputs(
    index: int = 1,
) -> tuple[MatchPass, MatchEventContext, MatchEventSpatial]:
    header = {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": f"event-{index}",
        "source_index": index,
        "period": 1,
        "timestamp_ms": index * 1000,
        "event_type": "Pass",
        "external_team_id": "team-1",
        "external_player_id": "player-1",
    }

    action = MatchPass.model_validate(header | {"outcome": "complete"})
    context = MatchEventContext.model_validate(
        header
        | {
            "position": {
                "external_position_id": "17",
                "position_name": "Right Wing",
            },
            "possession_sequence": 10,
            "external_possession_team_id": "team-1",
        }
    )
    spatial = MatchEventSpatial.model_validate(
        header
        | {
            "coordinate_system": "statsbomb_120_80",
            "start_location": {"x": 80.0, "y": 20.0},
            "end_location": {"x": 100.0, "y": 30.0},
        }
    )

    return action, context, spatial


def test_combines_action_context_and_coordinates() -> None:
    action, context, spatial = make_inputs()

    result = contextualize_actions(
        actions=[action],
        contexts=[context],
        spatial_events=[spatial],
    )

    assert len(result) == 1
    combined = result[0]
    assert isinstance(combined.action, MatchPass)
    assert combined.action.outcome == "complete"
    assert combined.context.position is not None
    assert combined.context.position.position_name == "Right Wing"
    assert combined.context.event_team_has_possession is True
    assert combined.spatial.start_location is not None
    assert combined.spatial.start_location.x == 80.0
    assert combined.spatial.end_location is not None
    assert combined.spatial.end_location.x == 100.0


def test_joins_by_id_and_orders_by_source_index() -> None:
    first_action, first_context, first_spatial = make_inputs(1)
    second_action, second_context, second_spatial = make_inputs(2)

    result = contextualize_actions(
        actions=[second_action, first_action],
        contexts=[first_context, second_context],
        spatial_events=[second_spatial, first_spatial],
    )

    assert [item.action.external_event_id for item in result] == ["event-1", "event-2"]


@pytest.mark.parametrize("missing", ["context", "spatial"])
def test_rejects_missing_corresponding_event(missing: str) -> None:
    action, context, spatial = make_inputs()

    with pytest.raises(ValueError, match="Missing"):
        contextualize_actions(
            actions=[action],
            contexts=[] if missing == "context" else [context],
            spatial_events=[] if missing == "spatial" else [spatial],
        )


@pytest.mark.parametrize("duplicate", ["actions", "contexts", "spatial"])
def test_rejects_duplicate_ids(duplicate: str) -> None:
    action, context, spatial = make_inputs()

    with pytest.raises(ValueError, match="Duplicate event IDs"):
        contextualize_actions(
            actions=[action, action] if duplicate == "actions" else [action],
            contexts=[context, context] if duplicate == "contexts" else [context],
            spatial_events=([spatial, spatial] if duplicate == "spatial" else [spatial]),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_index", 2),
        ("period", 2),
        ("timestamp_ms", 2000),
        ("event_type", "Shot"),
        ("external_team_id", "team-2"),
        ("external_player_id", "player-2"),
    ],
)
def test_rejects_inconsistent_context_header(field: str, value: object) -> None:
    action, context, spatial = make_inputs()
    data = context.model_dump()
    data[field] = value

    with pytest.raises(ValueError, match="Inconsistent event header"):
        contextualize_actions(
            actions=[action],
            contexts=[MatchEventContext.model_validate(data)],
            spatial_events=[spatial],
        )


def test_rejects_inconsistent_spatial_header() -> None:
    action, context, spatial = make_inputs()
    data = spatial.model_dump()
    data["timestamp_ms"] = 2000

    with pytest.raises(ValueError, match="Inconsistent event header"):
        contextualize_actions(
            actions=[action],
            contexts=[context],
            spatial_events=[MatchEventSpatial.model_validate(data)],
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "another-provider"),
        ("external_match_id", "another-match"),
    ],
)
def test_rejects_mixed_sources_or_matches(field: str, value: str) -> None:
    action, context, spatial = make_inputs()
    data = context.model_dump()
    data[field] = value

    with pytest.raises(ValueError, match="one provider and match"):
        contextualize_actions(
            actions=[action],
            contexts=[MatchEventContext.model_validate(data)],
            spatial_events=[spatial],
        )


def test_accepts_empty_inputs() -> None:
    assert (
        contextualize_actions(
            actions=[],
            contexts=[],
            spatial_events=[],
        )
        == []
    )
