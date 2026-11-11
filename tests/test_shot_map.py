from pathlib import Path

import pytest

from football_analytics.ingestion.action_context import ContextualizedAction
from football_analytics.integrations.base import (
    MatchEventContext,
    MatchEventSpatial,
    MatchShot,
)
from football_analytics.visualization.shot_map import (
    prepare_shot_map,
    render_shot_map,
    xg_marker_area,
)


def make_shot(
    index: int,
    *,
    period: int = 1,
    player_id: str = "player-1",
    team_id: str = "team-1",
    xg: float | None = None,
) -> ContextualizedAction:
    header = {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": f"shot-{index}",
        "source_index": index,
        "period": period,
        "timestamp_ms": index * 1000,
        "event_type": "Shot",
        "external_team_id": team_id,
        "external_player_id": player_id,
    }

    return ContextualizedAction(
        action=MatchShot.model_validate(header | {"outcome": "goal", "statsbomb_xg": xg}),
        context=MatchEventContext.model_validate(header),
        spatial=MatchEventSpatial.model_validate(
            header
            | {
                "coordinate_system": "statsbomb_120_80",
                "start_location": {"x": 108.1, "y": 40.1},
            }
        ),
    )


def test_includes_extra_time_and_excludes_shootout() -> None:
    points = prepare_shot_map(
        [
            make_shot(1, period=1),
            make_shot(2, period=3),
            make_shot(3, period=4),
            make_shot(4, period=5),
        ]
    )

    assert [point.event_id for point in points] == [
        "shot-1",
        "shot-2",
        "shot-3",
    ]


def test_filters_player_without_changing_coordinates() -> None:
    points = prepare_shot_map(
        [
            make_shot(1),
            make_shot(2, player_id="player-2"),
        ],
        player_id="player-1",
    )

    assert len(points) == 1
    assert points[0].player_id == "player-1"
    assert points[0].x == 108.1
    assert points[0].y == 40.1


def test_rejects_duplicate_shots() -> None:
    event = make_shot(1)

    with pytest.raises(ValueError, match="Duplicate shot"):
        prepare_shot_map([event, event])


def test_rejects_missing_start_location() -> None:
    event = make_shot(1)
    event.spatial.start_location = None

    with pytest.raises(ValueError, match="start location"):
        prepare_shot_map([event])


def test_exports_png(tmp_path: Path) -> None:
    output = tmp_path / "maps" / "shots.png"

    result = render_shot_map(
        [
            make_shot(1),
            make_shot(2, team_id="team-2", player_id="player-2"),
        ],
        team_names={"team-1": "Argentina", "team-2": "França"},
        output_path=output,
        title="Final de 2022",
    )

    assert result == output
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_rejects_unknown_team(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown team"):
        render_shot_map(
            [make_shot(1, team_id="another-team")],
            team_names={"team-1": "Argentina", "team-2": "França"},
            output_path=tmp_path / "shots.png",
            title="Final de 2022",
        )


@pytest.mark.parametrize("xg", [None, 0.0, 0.25])
def test_preserves_xg_in_map_points(xg: float | None) -> None:
    points = prepare_shot_map([make_shot(1, xg=xg)])

    assert points[0].statsbomb_xg == xg


def test_marker_area_is_proportional_to_xg() -> None:
    assert xg_marker_area(0.2) == 2 * xg_marker_area(0.1)


def test_zero_xg_has_zero_proportional_area() -> None:
    assert xg_marker_area(0.0) == 0.0


@pytest.mark.parametrize(
    "xg",
    [
        None,
        -0.01,
        1.01,
        float("nan"),
        float("inf"),
        float("-inf"),
        True,
    ],
)
def test_rejects_invalid_xg_for_marker_area(xg: float | None) -> None:
    with pytest.raises(ValueError):
        xg_marker_area(xg)
