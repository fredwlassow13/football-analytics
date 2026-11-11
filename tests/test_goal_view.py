from pathlib import Path

import pytest

from football_analytics.ingestion.action_context import ContextualizedAction
from football_analytics.integrations.base import (
    MatchEventContext,
    MatchEventSpatial,
    MatchShot,
)
from football_analytics.visualization.goal_view import (
    prepare_goal_view,
    render_goal_view,
)


def make_shot(
    index: int,
    *,
    outcome: str = "goal",
    end_x: float = 120.0,
    end_z: float | None = 0.5,
    period: int = 1,
) -> ContextualizedAction:
    header = {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": f"shot-{index}",
        "source_index": index,
        "period": period,
        "timestamp_ms": index * 1000,
        "event_type": "Shot",
        "external_team_id": "team-1",
        "external_player_id": "player-1",
    }

    return ContextualizedAction(
        action=MatchShot.model_validate(header | {"outcome": outcome}),
        context=MatchEventContext.model_validate(header),
        spatial=MatchEventSpatial.model_validate(
            header
            | {
                "coordinate_system": "statsbomb_120_80",
                "end_location": {"x": end_x, "y": 40.0, "z": end_z},
            }
        ),
    )


def test_preserves_depth_without_projecting_to_goal_plane() -> None:
    points = prepare_goal_view(
        [
            make_shot(1),
            make_shot(2, outcome="saved", end_x=109.7),
        ]
    )

    assert points[0].on_goal_plane is True
    assert points[1].on_goal_plane is False
    assert points[1].end_x == 109.7
    assert points[1].end_y == 40.0
    assert points[1].end_z == 0.5


def test_excludes_shootout_and_shots_not_on_target() -> None:
    points = prepare_goal_view(
        [
            make_shot(1),
            make_shot(2, period=5),
            make_shot(3, outcome="off_target"),
            make_shot(4, outcome="blocked", end_z=None),
        ]
    )

    assert [point.event_id for point in points] == ["shot-1"]


def test_rejects_on_target_shot_without_height() -> None:
    with pytest.raises(ValueError, match="destination with height"):
        prepare_goal_view([make_shot(1, end_z=None)])


def test_preserves_zero_height() -> None:
    points = prepare_goal_view([make_shot(1, end_z=0.0)])

    assert points[0].end_z == 0.0


def test_rejects_duplicate_shots() -> None:
    shot = make_shot(1)

    with pytest.raises(ValueError, match="Duplicate shot"):
        prepare_goal_view([shot, shot])


def test_exports_png(tmp_path: Path) -> None:
    output = tmp_path / "goal_view.png"

    result = render_goal_view(
        [
            make_shot(1),
            make_shot(2, outcome="saved", end_x=117.3),
        ],
        team_names={"team-1": "Argentina", "team-2": "França"},
        output_path=output,
        title="Destinos das finalizações",
    )

    assert result == output
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
