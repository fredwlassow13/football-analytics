from pathlib import Path

import pytest

from football_analytics.ingestion.player_activity import select_player_activity
from football_analytics.ingestion.player_activity_heatmap import (
    build_player_activity_heatmap,
)
from football_analytics.integrations.base import MatchEventSpatial
from football_analytics.visualization.player_activity_heatmap import (
    render_player_activity_heatmap,
)


def make_heatmap(*, empty: bool = False):
    events = (
        []
        if empty
        else [
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
                    "start_location": {"x": x, "y": 5.0},
                }
            )
            for index, event_type, x in [
                (1, "Pass", 5.0),
                (2, "Pressure", 5.0),
                (3, "Carry", 15.0),
            ]
        ]
    )
    selection = select_player_activity(
        events,
        provider="statsbomb",
        match_id="3869685",
        team_id="779",
        player_id="5503",
    )
    return build_player_activity_heatmap(selection)


@pytest.mark.parametrize("extension", ["png", "svg"])
def test_exports_activity_map_and_composition(
    tmp_path: Path,
    extension: str,
) -> None:
    heatmap = make_heatmap()
    before = heatmap.model_dump()
    output = tmp_path / f"activity.{extension}"

    render_player_activity_heatmap(
        heatmap,
        player_name="Lionel Messi",
        team_name="Argentina",
        output_path=output,
    )

    assert heatmap.model_dump() == before
    if extension == "png":
        assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    else:
        svg = output.read_text(encoding="utf-8")
        assert "3 ações registradas" in svg
        assert "Passes tentados" in svg
        assert "Pressões" in svg
        assert "Conduções" in svg
        assert "Composição da amostra" in svg


def test_exports_percentage_distribution(tmp_path: Path) -> None:
    output = tmp_path / "percentage.svg"

    render_player_activity_heatmap(
        make_heatmap(),
        player_name="Lionel Messi",
        team_name="Argentina",
        output_path=output,
        mode="percentage",
    )

    svg = output.read_text(encoding="utf-8")
    assert "66.7%" in svg
    assert "33.3%" in svg


def test_reports_empty_activity_sample(tmp_path: Path) -> None:
    output = tmp_path / "empty.svg"

    render_player_activity_heatmap(
        make_heatmap(empty=True),
        player_name="Lionel Messi",
        team_name="Argentina",
        output_path=output,
    )

    svg = output.read_text(encoding="utf-8")
    assert "0 ações registradas" in svg
    assert "Sem ações selecionadas neste recorte" in svg
