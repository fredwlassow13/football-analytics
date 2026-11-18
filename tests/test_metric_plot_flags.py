import json

import pytest
from matplotlib.offsetbox import AnnotationBbox
from test_metric_plot import prepared
from test_metric_plot_data import observation, value
from test_team_flags import catalogue as catalogue_fixture

from football_analytics.visualization.metric_plot import render_metric_plot, write_plot_bundle
from football_analytics.visualization.metric_plot_contracts import PlotRequest
from football_analytics.visualization.team_flags import resolve_team_flags

catalogue = catalogue_fixture


def request(**changes):
    return PlotRequest(
        **(
            dict(
                kind="scatter",
                group_by="player",
                x_metric="passes.attempted",
                y_metric="shots.total",
                style={"identity_style": "flag"},
            )
            | changes
        )
    )


def rows():
    return [
        observation([value(number=10), value("shots.total", 2)]),
        observation([value(number=20), value("shots.total", 4)], match=2),
    ]


def test_flags_replace_symbols_and_are_centered_at_exact_coordinates(catalogue):
    from matplotlib.lines import Line2D
    from matplotlib.offsetbox import AnchoredOffsetbox

    config = request()
    data = prepared(config, rows())
    figure = render_metric_plot(data, flags=resolve_team_flags(catalogue, ("1",)))
    axes = figure.axes[0]
    assert not axes.collections
    annotations = [a for a in axes.artists if isinstance(a, AnnotationBbox)]
    assert len(annotations) == 2
    assert [a.xy for a in annotations] == [(10.0, 2.0), (20.0, 4.0)]
    assert all(a.xybox == (0.0, 0.0) for a in annotations)
    assert axes.get_xlim()[1] > 20
    assert axes.get_ylim()[1] > 4
    figure.canvas.draw()
    for annotation in annotations:
        bbox = annotation.offsetbox.get_window_extent()
        expected = axes.transData.transform(annotation.xy)
        assert ((bbox.x0 + bbox.x1) / 2, (bbox.y0 + bbox.y1) / 2) == pytest.approx(expected)
    legend = next(a for a in axes.artists if isinstance(a, AnchoredOffsetbox))
    assert not legend.findobj(match=Line2D)
    figure.clear()


def test_unknown_team_keeps_symbol_and_records_fallback(catalogue, tmp_path):
    row = rows()[0].model_copy(update={"team_id": "999", "team_name": "Unknown"})
    data = prepared(request(), [row])
    output = tmp_path / "fallback"
    _, metadata = write_plot_bundle(data, output, flag_catalogue=catalogue)
    content = json.loads(metadata.read_text())
    assert content["identity_assets"]["fallbacks"] == {"999": "team_flag_not_registered"}
    assert len(content["data"]["points"]) == 1


@pytest.mark.parametrize("format", ["png", "svg"])
def test_bundle_preserves_flag_hashes_and_plot_recipe(catalogue, tmp_path, format):
    data = prepared(request(output_format=format), rows())
    _, metadata = write_plot_bundle(data, tmp_path / f"bundle-{format}", flag_catalogue=catalogue)
    content = json.loads(metadata.read_text())
    assert content["data"]["request"]["plot_version"] == "1.3.0"
    assert content["data"]["request"]["style"]["identity_style"] == "flag"
    asset = content["identity_assets"]["used"][0]
    assert asset["team_id"] == "1"
    assert asset["png"]["sha256"] == json.loads(catalogue.read_text())["teams"][0]["png"]["sha256"]
    assert not content["identity_assets"]["fallbacks"]


def test_corrupt_flag_stops_before_writing_bundle(catalogue, tmp_path):
    (catalogue.parent / "png/1.png").write_bytes(b"invalid")
    output = tmp_path / "corrupt"
    with pytest.raises(ValueError, match="hash/size mismatch"):
        write_plot_bundle(prepared(request(), rows()), output, flag_catalogue=catalogue)
    assert not output.exists()


def test_old_plot_version_remains_supported_for_symbols():
    old = request(plot_version="1.0.0", style={"identity_style": "symbol"})
    figure = render_metric_plot(prepared(old, rows()))
    assert len(figure.axes[0].collections) == 1
    figure.clear()


@pytest.mark.parametrize(
    "changes",
    [
        {"plot_version": "1.0.0"},
        {"plot_version": "1.1.0"},
        {"size_metric": "passes.completed"},
        {"kind": "bar", "y_metric": None, "size_metric": None},
        {"style": {"identity_style": "crest"}},
    ],
)
def test_unsupported_flag_configuration_is_rejected(changes):
    with pytest.raises(ValueError):
        request(**changes)


@pytest.mark.parametrize(
    "style",
    [
        {"identity_style": "flag", "flag_offset": (5.0, 0.0)},
        {"identity_style": "flag", "team_flag_offsets": {"1": (18.0, -12.0)}},
    ],
)
def test_centered_flags_reject_offsets(style):
    with pytest.raises(ValueError, match="cannot have position offsets"):
        request(style=style)


def test_centered_flag_version_1_2_remains_supported(catalogue):
    data = prepared(request(plot_version="1.2.0"), rows())
    figure = render_metric_plot(data, flags=resolve_team_flags(catalogue, ("1",)))
    assert not figure.axes[0].collections
    assert len([a for a in figure.axes[0].artists if isinstance(a, AnnotationBbox)]) == 2
    figure.clear()
