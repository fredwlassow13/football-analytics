import pytest

from football_analytics.ingestion.player_activity import select_player_activity
from football_analytics.ingestion.player_activity_heatmap import (
    build_player_activity_heatmap,
)
from football_analytics.integrations.base import MatchEventSpatial


def make_selection(
    samples: list[tuple[int, str, float, float]],
):
    events = [
        MatchEventSpatial.model_validate(
            {
                "provider": "statsbomb",
                "external_match_id": "3869685",
                "external_event_id": f"event-{index}",
                "source_index": index,
                "period": 1,
                "timestamp_ms": index * 1000,
                "event_type": event_type,
                "external_team_id": "779",
                "external_player_id": "5503",
                "coordinate_system": "statsbomb_120_80",
                "start_location": {"x": x, "y": y},
            }
        )
        for index, event_type, x, y in samples
    ]
    return select_player_activity(
        events,
        provider="statsbomb",
        match_id="3869685",
        team_id="779",
        player_id="5503",
    )


def test_aggregates_different_actions_without_merging_events() -> None:
    selection = make_selection(
        [
            (3, "Pass", 5.0, 5.0),
            (1, "Ball Receipt*", 5.0, 5.0),
            (2, "Pressure", 5.0, 5.0),
            (4, "Carry", 15.0, 5.0),
        ]
    )
    result = build_player_activity_heatmap(selection)

    assert result.total_events == 4
    assert result.cells[0].external_event_ids == ("event-1", "event-2", "event-3")
    assert result.cells[1].external_event_ids == ("event-4",)
    assert result.counts_by_type == {
        "Ball Receipt*": 1,
        "Carry": 1,
        "Pass": 1,
        "Pressure": 1,
    }
    assert result.percentages is not None
    assert result.percentages[0] == 75.0
    assert result.percentages[1] == 25.0


def test_assigns_pitch_edges_to_last_cell() -> None:
    result = build_player_activity_heatmap(make_selection([(1, "Duel", 120.0, 80.0)]))

    assert len(result.cells) == 96
    assert result.cells[-1].external_event_ids == ("event-1",)
    assert result.total_events == 1


def test_empty_selection_has_no_percentage_distribution() -> None:
    result = build_player_activity_heatmap(make_selection([]))

    assert len(result.cells) == 96
    assert result.total_events == 0
    assert result.percentages is None
    assert result.counts_by_type == {}


def test_blocks_unresolved_location_issues() -> None:
    selection = make_selection([(1, "Pass", -0.1, 20.0)])

    with pytest.raises(ValueError, match="require review"):
        build_player_activity_heatmap(selection)


@pytest.mark.parametrize("columns", [0, -1, True, 121])
def test_rejects_invalid_grid(columns: int) -> None:
    with pytest.raises(ValueError, match="grid dimensions"):
        build_player_activity_heatmap(
            make_selection([]),
            columns=columns,
        )
