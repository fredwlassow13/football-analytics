from pathlib import Path

import pytest

from football_analytics.ingestion.action_context import ContextualizedAction
from football_analytics.integrations.base import (
    MatchEventContext,
    MatchEventSpatial,
    MatchShot,
)
from football_analytics.visualization.off_target_view import (
    prepare_off_target_view,
    render_off_target_view,
)


def make_shot(
    index: int,
    *,
    outcome: str = "off_target",
    end_x: float = 120.0,
    end_z: float | None = 7.5,
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
                "end_location": {"x": end_x, "y": 28.0, "z": end_z},
            }
        ),
    )


def test_preserves_coordinates_without_projecting_depth() -> None:
    points = prepare_off_target_view([make_shot(1, end_x=119.2)])

    assert len(points) == 1
    assert points[0].end_x == 119.2
    assert points[0].end_y == 28.0
    assert points[0].end_z == 7.5
    assert points[0].on_goal_plane is False


def test_selects_only_off_target_shots_from_playing_periods() -> None:
    points = prepare_off_target_view(
        [
            make_shot(1),
            make_shot(2, period=5),
            make_shot(3, outcome="goal"),
            make_shot(4, outcome="saved"),
            make_shot(5, outcome="blocked", end_z=None),
        ]
    )

    assert [point.event_id for point in points] == ["shot-1"]


def test_rejects_missing_height() -> None:
    with pytest.raises(ValueError, match="destination with height"):
        prepare_off_target_view([make_shot(1, end_z=None)])


def test_rejects_duplicate_shots() -> None:
    shot = make_shot(1)

    with pytest.raises(ValueError, match="Duplicate shot"):
        prepare_off_target_view([shot, shot])


def test_exports_png(tmp_path: Path) -> None:
    output = tmp_path / "off_target.png"

    result = render_off_target_view(
        [make_shot(1)],
        team_names={"team-1": "Argentina", "team-2": "França"},
        player_names={"player-1": "Jogador"},
        output_path=output,
        title="Finalizações para fora",
    )

    assert result == output
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_rejects_unknown_team(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown team"):
        render_off_target_view(
            [make_shot(1)],
            team_names={"another-team": "Argentina", "team-2": "França"},
            player_names={"player-1": "Jogador"},
            output_path=tmp_path / "off_target.png",
            title="Finalizações para fora",
        )
