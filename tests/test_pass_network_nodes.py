import pytest

from football_analytics.ingestion.action_context import ContextualizedAction
from football_analytics.ingestion.pass_network_nodes import build_pass_network_nodes
from football_analytics.integrations.base import (
    MatchEventContext,
    MatchEventSpatial,
    MatchPass,
)


def make_pass(
    index: int,
    *,
    x: float = 10.0,
    y: float = 20.0,
    outcome: str = "complete",
    period: int = 1,
    team_id: str = "team-1",
    player_id: str = "player-1",
) -> ContextualizedAction:
    header = {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": f"pass-{index}",
        "source_index": index,
        "period": period,
        "timestamp_ms": index * 1000,
        "event_type": "Pass",
        "external_team_id": team_id,
        "external_player_id": player_id,
    }

    return ContextualizedAction(
        action=MatchPass.model_validate(header | {"outcome": outcome}),
        context=MatchEventContext.model_validate(header),
        spatial=MatchEventSpatial.model_validate(
            header
            | {
                "coordinate_system": "statsbomb_120_80",
                "start_location": {"x": x, "y": y},
            }
        ),
    )


def test_uses_all_pass_attempts_and_preserves_ordered_sources() -> None:
    nodes = build_pass_network_nodes(
        [
            make_pass(2, x=30.0, y=40.0, outcome="incomplete"),
            make_pass(1, x=10.0, y=20.0),
        ]
    )

    assert len(nodes) == 1
    node = nodes[0]
    assert node.external_player_id == "player-1"
    assert node.mean_x == 20.0
    assert node.mean_y == 30.0
    assert node.pass_attempts == 2
    assert node.external_event_ids == ("pass-1", "pass-2")


def test_includes_extra_time_and_excludes_shootout() -> None:
    nodes = build_pass_network_nodes(
        [
            make_pass(1, period=3),
            make_pass(2, period=4),
            make_pass(3, period=5, x=100.0, y=70.0),
        ]
    )

    assert nodes[0].pass_attempts == 2
    assert nodes[0].mean_x == 10.0
    assert nodes[0].mean_y == 20.0


def test_rejects_missing_location() -> None:
    event = make_pass(1)
    event.spatial.start_location = None

    with pytest.raises(ValueError, match="start location"):
        build_pass_network_nodes([event])


def test_reuses_duplicate_event_validation() -> None:
    event = make_pass(1)

    with pytest.raises(ValueError, match="Duplicate pass event"):
        build_pass_network_nodes([event, event])


def test_separates_players_and_teams() -> None:
    nodes = build_pass_network_nodes(
        [
            make_pass(1),
            make_pass(2, player_id="player-2", x=30.0),
            make_pass(
                3,
                team_id="team-2",
                player_id="player-3",
                x=50.0,
            ),
        ]
    )

    by_player = {node.external_player_id: node for node in nodes}

    assert len(nodes) == 3
    assert by_player["player-1"].mean_x == 10.0
    assert by_player["player-2"].mean_x == 30.0
    assert by_player["player-3"].mean_x == 50.0
    assert by_player["player-3"].external_team_id == "team-2"


def test_accepts_empty_input() -> None:
    assert build_pass_network_nodes([]) == []
