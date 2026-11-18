import hashlib
import json
from uuid import uuid4

import pytest
from test_metric_plot_data import observation, value

from football_analytics.analytics.catalogue import get_metric
from football_analytics.visualization.metric_plot import render_metric_plot, write_plot_bundle
from football_analytics.visualization.metric_plot_contracts import (
    PlotRequest,
    PlotSource,
    PreparedPlot,
)
from football_analytics.visualization.metric_plot_data import select_observations


def prepared(request, rows):
    points, excluded, omitted = select_observations(rows, request)
    matches = sorted({mid for row in rows for mid in row.match_ids})
    return PreparedPlot(
        request=request,
        batch_id=uuid4(),
        index_sha256="0" * 64,
        sources=tuple(
            PlotSource(match_id=mid, export_id=uuid4(), file=f"{mid}.json", sha256="0" * 64)
            for mid in matches
        ),
        definitions=tuple(get_metric(mid) for mid in request.metric_ids),
        population_count=len(points) + len(excluded) + omitted,
        points=points,
        excluded=excluded,
        omitted_by_top_n=omitted,
    )


def test_bars_render_selected_values_and_unit_labels():
    request = PlotRequest(kind="bar", group_by="player", x_metric="passes.attempted")
    data = prepared(
        request, [observation([value(number=2)]), observation([value(number=7)], match=2)]
    )
    figure = render_metric_plot(data)
    axes = figure.axes[0]
    assert [bar.get_width() for bar in axes.patches] == [7, 2]
    assert "contagem" in axes.get_xlabel()
    assert "jogador × partida" in figure.texts[0].get_text()
    figure.clear()


def test_scatter_preserves_coordinates_and_custom_color_and_marker():
    request = PlotRequest(
        kind="scatter",
        group_by="player",
        x_metric="passes.attempted",
        y_metric="shots.total",
        style={"team_colors": {"1": "#ff0000"}, "team_markers": {"1": "s"}},
    )
    data = prepared(request, [observation([value(number=10), value("shots.total", 2)])])
    figure = render_metric_plot(data)
    collection = figure.axes[0].collections[0]
    assert collection.get_offsets().tolist() == [[10.0, 2.0]]
    assert collection.get_facecolors()[0][:3].tolist() == [1.0, 0.0, 0.0]
    assert len(collection.get_paths()[0].vertices) == 5
    figure.clear()


def test_histogram_counts_observations_not_metric_sum():
    request = PlotRequest(kind="histogram", group_by="player", x_metric="passes.attempted", bins=3)
    data = prepared(request, [observation([value(number=n)], match=n + 1) for n in (0, 1, 5)])
    figure = render_metric_plot(data)
    assert sum(p.get_height() for p in figure.axes[0].patches) == 3
    figure.clear()


@pytest.mark.parametrize("format", ["png", "svg"])
def test_bundle_contains_image_hash_and_full_reproducible_data(format, tmp_path):
    request = PlotRequest(
        kind="bar", group_by="player", x_metric="passes.attempted", output_format=format
    )
    data = prepared(request, [observation([value(number=2)])])
    output = tmp_path / "bundle"
    image, metadata = write_plot_bundle(data, output)
    content = json.loads(metadata.read_text())
    assert content["image_sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert PreparedPlot.model_validate_json(json.dumps(content["data"])) == data
    before = image.read_bytes()
    with pytest.raises(FileExistsError):
        write_plot_bundle(data, output)
    assert image.read_bytes() == before


def test_empty_numeric_population_is_not_rendered_as_zero(tmp_path):
    request = PlotRequest(kind="bar", group_by="player", x_metric="passes.attempted")
    data = prepared(request, [observation([value(number=None, status="unknown")])])
    output = tmp_path / "empty"
    with pytest.raises(ValueError, match="No plottable observations"):
        write_plot_bundle(data, output)
    assert not output.exists()
