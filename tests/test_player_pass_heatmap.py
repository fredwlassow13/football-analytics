import pytest

from football_analytics.ingestion.player_pass_heatmap import (
    build_player_pass_heatmap,
)
from football_analytics.integrations.base import MatchEventSpatial


def make_event(
    index: int,
    *,
    x: float = 5.0,
    y: float = 5.0,
    period: int = 1,
    player_id: str = "player-1",
    event_type: str = "Pass",
) -> MatchEventSpatial:
    return MatchEventSpatial.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_event_id": f"event-{index}",
            "source_index": index,
            "period": period,
            "timestamp_ms": index * 1000,
            "event_type": event_type,
            "external_team_id": "team-1",
            "external_player_id": player_id,
            "coordinate_system": "statsbomb_120_80",
            "start_location": {"x": x, "y": y},
        }
    )


def build(events: list[MatchEventSpatial]):
    return build_player_pass_heatmap(
        events,
        provider="statsbomb",
        match_id="3869685",
        team_id="team-1",
        player_id="player-1",
    )


def test_counts_cells_and_preserves_ordered_event_ids() -> None:
    result = build([make_event(2), make_event(1), make_event(3, x=15.0)])

    assert len(result.cells) == 96
    assert result.cells[0].external_event_ids == ("event-1", "event-2")
    assert result.cells[1].external_event_ids == ("event-3",)
    assert result.total_events == 3
    assert result.located_count == 3
    assert result.percentages is not None
    assert result.percentages[0] == pytest.approx(200 / 3)
    assert sum(result.percentages) == pytest.approx(100)


@pytest.mark.parametrize(
    ("x", "y", "column", "row"),
    [
        (0.0, 0.0, 0, 0),
        (10.0, 10.0, 1, 1),
        (120.0, 80.0, 11, 7),
    ],
)
def test_assigns_boundary_coordinates_once(
    x: float,
    y: float,
    column: int,
    row: int,
) -> None:
    result = build([make_event(1, x=x, y=y)])
    occupied = [cell for cell in result.cells if cell.count]

    assert len(occupied) == 1
    assert occupied[0].column == column
    assert occupied[0].row == row
    assert result.located_count == 1


def test_keeps_outside_coordinates_separate_without_clipping() -> None:
    result = build(
        [
            make_event(1, x=-0.1),
            make_event(2, x=120.1),
            make_event(3),
        ]
    )

    assert result.outside_pitch_event_ids == ("event-1", "event-2")
    assert result.total_events == 3
    assert result.located_count == 1
    assert result.cells[0].count == 1


def test_filters_player_event_type_and_shootout() -> None:
    result = build(
        [
            make_event(1),
            make_event(2, player_id="player-2"),
            make_event(3, event_type="Shot"),
            make_event(4, period=5),
            make_event(5, period=4),
        ]
    )

    assert result.cells[0].external_event_ids == ("event-1", "event-5")
    assert result.total_events == 2


def test_empty_sample_has_no_percentage_distribution() -> None:
    result = build([])

    assert len(result.cells) == 96
    assert result.total_events == 0
    assert result.percentages is None


def test_rejects_duplicate_events() -> None:
    event = make_event(1)
    with pytest.raises(ValueError, match="Duplicate"):
        build([event, event])


def test_rejects_missing_start_location() -> None:
    event = make_event(1)
    event.start_location = None

    with pytest.raises(ValueError, match="start location"):
        build([event])


def test_rejects_another_match() -> None:
    event = make_event(1)
    event.external_match_id = "another-match"

    with pytest.raises(ValueError, match="requested provider and match"):
        build([event])


@pytest.mark.parametrize("columns", [0, -1, True, 121])
def test_rejects_invalid_grid(columns: int) -> None:
    with pytest.raises(ValueError, match="grid dimensions"):
        build_player_pass_heatmap(
            [],
            provider="statsbomb",
            match_id="3869685",
            team_id="team-1",
            player_id="player-1",
            columns=columns,
        )


def test_filters_selected_periods() -> None:
    result = build_player_pass_heatmap(
        [make_event(1), make_event(2, period=2)],
        provider="statsbomb",
        match_id="3869685",
        team_id="team-1",
        player_id="player-1",
        periods=(2,),
    )

    assert result.total_events == 1
    assert result.cells[0].external_event_ids == ("event-2",)
