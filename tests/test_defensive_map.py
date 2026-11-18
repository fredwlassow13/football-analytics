from pathlib import Path

import pytest
from test_defensive_summary import aerial, header

from football_analytics.ingestion.defensive_map import build_defensive_map
from football_analytics.integrations.base import (
    MatchClearance,
    MatchEvent,
    MatchEventSpatial,
    MatchPressure,
)
from football_analytics.visualization.defensive_map import render_defensive_maps


def spatial(source, location=(10.0, 10.0)):
    return MatchEventSpatial.model_validate(
        source.model_dump(include=set(MatchEvent.model_fields))
        | {
            "coordinate_system": "statsbomb_120_80",
            "start_location": {"x": location[0], "y": location[1]}
            if location is not None
            else None,
        }
    )


def build(actions, aerials=(), points=None, **kwargs):
    return build_defensive_map(
        actions,
        aerials,
        [spatial(item) for item in {a.external_event_id: a for a in [*actions, *aerials]}.values()]
        if points is None
        else points,
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
        team_id="779",
        available_periods=(1, 2, 3, 4),
        **kwargs,
    )


def test_shared_clearance_is_mapped_once_and_aerial_pass_is_preserved() -> None:
    action = MatchClearance.model_validate(header(1, "Clearance") | {"aerial_won": True})
    result = build([action], [aerial(1, event_type="Clearance"), aerial(2)])
    assert result.candidate_event_ids == ("event-1", "event-2")
    assert result.located_count == 2
    assert sum(c.count for c in result.cells) == 2
    assert result.percentages is not None
    assert sum(result.percentages) == 100
    assert result.candidate_counts_by_type == {"Clearance": 1, "Pass": 1}


def test_groups_types_periods_player_and_team_intersect() -> None:
    actions = [
        MatchPressure.model_validate(header(1, "Pressure")),
        MatchPressure.model_validate(header(2, "Pressure", period=2)),
        MatchPressure.model_validate(header(3, "Pressure", external_player_id="p2")),
        MatchPressure.model_validate(
            header(4, "Pressure", external_team_id="771", external_player_id="p3")
        ),
    ]
    result = build(
        actions, group="pressure", event_types=("Pressure",), periods=(1,), player_id="player-1"
    )
    assert result.candidate_event_ids == ("event-1",)
    assert build(actions, group="fouls").candidate_count == 0
    assert build(actions, group="aerials").candidate_count == 0


def test_aerial_filter_does_not_include_non_aerial_clearance() -> None:
    actions = [MatchClearance.model_validate(header(1, "Clearance"))]
    assert build(actions, [aerial(2)], group="aerials").candidate_event_ids == ("event-2",)


def test_location_coverage_does_not_invent_or_clip_positions() -> None:
    actions = [MatchPressure.model_validate(header(i, "Pressure")) for i in range(1, 5)]
    result = build(
        actions,
        points=[
            spatial(actions[0], None),
            spatial(actions[1], (-1, 40)),
            spatial(actions[2], (120, 80)),
            spatial(actions[3], (0, 0)),
        ],
    )
    assert result.candidate_count == 4
    assert result.located_count == 2
    assert [i.reason for i in result.location_issues] == ["missing_location", "outside_pitch"]
    assert result.cells[-1].external_event_ids == ("event-3",)
    assert result.cells[0].external_event_ids == ("event-4",)
    assert sum(result.percentages) == 100


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(group="invalid"),
        dict(event_types=()),
        dict(event_types=("Shot", "Shot")),
        dict(event_types=("Carry",)),
        dict(periods=(5,)),
        dict(periods=(1, 1)),
        dict(periods=(True,)),
        dict(columns=0),
        dict(rows=True),
    ],
)
def test_rejects_invalid_selection(kwargs) -> None:
    with pytest.raises(ValueError):
        build([], **kwargs)


def test_rejects_missing_duplicate_and_mismatched_spatial_sources() -> None:
    action = MatchPressure.model_validate(header(1, "Pressure"))
    with pytest.raises(ValueError, match="Missing"):
        build([action], points=[])
    with pytest.raises(ValueError, match="Duplicate"):
        build([action], points=[spatial(action), spatial(action)])
    with pytest.raises(ValueError, match="headers differ"):
        build([action], points=[spatial(action).model_copy(update={"timestamp_ms": 999})])


def test_empty_and_shootout_separation() -> None:
    action = MatchPressure.model_validate(header(1, "Pressure", period=5))
    result = build([action])
    assert result.candidate_count == 0
    assert result.percentages is None


@pytest.mark.parametrize("suffix", ["png", "svg"])
def test_renders_empty_and_nonempty_with_shared_scale(tmp_path: Path, suffix: str) -> None:
    action = MatchPressure.model_validate(header(1, "Pressure"))
    path = tmp_path / f"map.{suffix}"
    render_defensive_maps(
        [build([action]), build([])], labels=["Argentina", "França"], output_path=path
    )
    assert path.stat().st_size > 1000
    if suffix == "svg":
        assert "Escala e grade comuns" in path.read_text()


def test_renderer_rejects_clipped_scale_and_tampered_cells(tmp_path: Path) -> None:
    result = build([MatchPressure.model_validate(header(1, "Pressure"))])
    with pytest.raises(ValueError, match="Shared scale"):
        render_defensive_maps(
            [result], labels=["Argentina"], output_path=tmp_path / "a.png", scale_max=0.5
        )
    corrupted = result.model_copy(update={"cells": ()})
    with pytest.raises(ValueError, match="grid differs"):
        render_defensive_maps([corrupted], labels=["Argentina"], output_path=tmp_path / "a.png")
    with pytest.raises(ValueError, match="same source and grid"):
        render_defensive_maps(
            [result, result.model_copy(update={"rows": 9})],
            labels=["a", "b"],
            output_path=tmp_path / "a.png",
        )


def test_percentage_renderer_uses_located_events_only(tmp_path: Path) -> None:
    actions = [MatchPressure.model_validate(header(i, "Pressure")) for i in (1, 2)]
    result = build(actions, points=[spatial(actions[0]), spatial(actions[1], None)])
    assert max(result.percentages) == 100
    output = tmp_path / "percentage.svg"
    render_defensive_maps([result], labels=["Argentina"], output_path=output, mode="percentage")
    assert "% dos eventos localizados" in output.read_text()
    assert "1/2 eventos localizados" in output.read_text()


def test_player_team_mismatch_is_rejected() -> None:
    action = MatchPressure.model_validate(header(1, "Pressure", external_team_id="771"))
    with pytest.raises(ValueError, match="another team"):
        build([action], player_id="player-1")


def test_group_filters_remain_ordered_and_recompose_periods() -> None:
    actions = [
        MatchPressure.model_validate(header(2, "Pressure", period=2)),
        MatchClearance.model_validate(header(1, "Clearance")),
    ]
    assert build(actions).candidate_event_ids == ("event-1", "event-2")
    first = build(actions, periods=(1,))
    second = build(actions, periods=(2,))
    assert first.candidate_count + second.candidate_count == build(actions).candidate_count
    assert build(actions, group="blocks_clearances").candidate_event_ids == ("event-1",)
