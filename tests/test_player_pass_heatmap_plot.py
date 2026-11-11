from pathlib import Path

import pytest

from football_analytics.ingestion.player_pass_heatmap import (
    HeatmapCell,
    PlayerPassHeatmap,
)
from football_analytics.visualization.player_pass_heatmap import (
    render_player_pass_heatmap,
)


def make_heatmap(*, empty: bool = False) -> PlayerPassHeatmap:
    return PlayerPassHeatmap(
        provider="statsbomb",
        external_match_id="3869685",
        external_team_id="779",
        external_player_id="5503",
        periods=(1, 2, 3, 4),
        columns=2,
        rows=2,
        cells=(
            HeatmapCell(
                row=0,
                column=0,
                external_event_ids=() if empty else ("pass-1", "pass-2"),
            ),
            HeatmapCell(row=0, column=1, external_event_ids=()),
            HeatmapCell(
                row=1,
                column=0,
                external_event_ids=() if empty else ("pass-3",),
            ),
            HeatmapCell(row=1, column=1, external_event_ids=()),
        ),
        outside_pitch_event_ids=(),
    )


@pytest.mark.parametrize("extension", ["png", "svg"])
def test_exports_heatmap_without_changing_source(
    tmp_path: Path,
    extension: str,
) -> None:
    heatmap = make_heatmap()
    before = heatmap.model_dump()
    output = tmp_path / f"heatmap.{extension}"

    render_player_pass_heatmap(
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
        assert "Lionel Messi" in svg
        assert "3 passes tentados" in svg
        assert "não mede tempo de permanência" in svg


def test_exports_percentages(tmp_path: Path) -> None:
    output = tmp_path / "percentages.svg"

    render_player_pass_heatmap(
        make_heatmap(),
        player_name="Lionel Messi",
        team_name="Argentina",
        output_path=output,
        mode="percentage",
    )

    svg = output.read_text(encoding="utf-8")
    assert "66.7%" in svg
    assert "33.3%" in svg


def test_reports_empty_sample(tmp_path: Path) -> None:
    output = tmp_path / "empty.svg"

    render_player_pass_heatmap(
        make_heatmap(empty=True),
        player_name="Lionel Messi",
        team_name="Argentina",
        output_path=output,
    )

    svg = output.read_text(encoding="utf-8")
    assert "0 passes tentados" in svg
    assert "Sem origens de passe dentro do campo neste recorte" in svg


def test_rejects_incomplete_grid(tmp_path: Path) -> None:
    data = make_heatmap().model_dump()
    data["cells"] = data["cells"][:-1]
    heatmap = PlayerPassHeatmap.model_validate(data)

    with pytest.raises(ValueError, match="complete grid"):
        render_player_pass_heatmap(
            heatmap,
            player_name="Lionel Messi",
            team_name="Argentina",
            output_path=tmp_path / "invalid.svg",
        )
