import json
from math import isnan

import pytest
from test_metric_plot import prepared
from test_metric_plot_data import observation, value

from football_analytics.visualization.advanced_metric_plots import (
    advanced_analysis,
    correlation_summary,
    line_series,
)
from football_analytics.visualization.metric_plot import render_metric_plot, write_plot_bundle
from football_analytics.visualization.metric_plot_contracts import PlotRequest


def request(kind="correlation", **changes):
    base = dict(kind=kind, group_by="team", x_metric="passes.attempted")
    if kind in {"correlation", "pair"}:
        base["additional_metrics"] = ("shots.total",)
    if kind == "line":
        base.update(x_metric=None, y_metric="passes.attempted", team_ids=("1",))
    return PlotRequest(**(base | changes))


def row(numbers, match=1):
    return observation(
        [value(metric, number) for metric, number in numbers], match=match, player=None
    )


def two_columns(right):
    return [
        row([("passes.attempted", n), ("shots.total", y)], match=n) for n, y in enumerate(right, 1)
    ]


@pytest.mark.parametrize("right,expected", [([2, 4, 6], 1.0), ([6, 4, 2], -1.0)])
def test_pearson_known_positive_and_negative_relationship(right, expected):
    result = correlation_summary(prepared(request(), two_columns(right)))
    assert result["matrix"][0][1] == pytest.approx(expected)
    assert result["matrix"][1][0] == pytest.approx(expected)
    assert result["n"] == 3


def test_constant_metric_is_null_including_its_diagonal():
    result = correlation_summary(prepared(request(), two_columns([4, 4, 4])))
    assert result["matrix"] == [[1.0, None], [None, None]]
    assert "shots.total:constant" in result["warnings"]


def test_insufficient_population_is_not_a_zero_correlation():
    result = correlation_summary(prepared(request(), two_columns([2, 4])))
    assert result["matrix"] == [[None, None], [None, None]]
    assert "insufficient_observations" in result["warnings"]


def test_multivariate_plots_use_one_complete_population():
    rows = two_columns([2, 4, 6])
    missing = row([("passes.attempted", 4), ("shots.total", 8)], match=4)
    missing = missing.model_copy(
        update={
            "values": (
                missing.values[0],
                value("shots.total", None, status="unknown"),
            )
        }
    )
    data = prepared(request(), [*rows, missing])
    assert len(data.excluded) == 1
    assert correlation_summary(data)["n"] == 3


def test_box_plot_preserves_outliers_and_reports_quartiles():
    data = prepared(
        request("box"),
        [row([("passes.attempted", n)], match=i) for i, n in enumerate((0, 1, 2, 3, 100), 1)],
    )
    groups = advanced_analysis(data)["groups"]
    assert groups[0]["q1"] == 1
    assert groups[0]["median"] == 2
    assert groups[0]["q3"] == 3
    assert groups[0]["whisker_low"] == 0
    assert groups[0]["whisker_high"] == 3
    assert groups[0]["outliers"] == [100]
    figure = render_metric_plot(data)
    assert any(list(line.get_ydata()) == [100] for line in figure.axes[0].lines)
    assert "n=5" in figure.axes[0].get_xticklabels()[0].get_text()
    figure.clear()


def test_line_uses_index_order_and_preserves_unknown_gap():
    rows = [row([("passes.attempted", 5)], match=1), row([("passes.attempted", 7)], match=3)]
    unknown = row([("passes.attempted", 0)], match=2).model_copy(
        update={
            "values": (value(number=None, status="unknown"),),
        }
    )
    data = prepared(request("line"), [*rows, unknown])
    by_id = {s.match_id: s for s in data.sources}
    data = data.model_copy(update={"sources": tuple(by_id[i] for i in (3, 2, 1))})
    series = line_series(data)[0]
    assert series["match_ids"] == [3, 2, 1]
    assert series["values"] == [7, None, 5]
    figure = render_metric_plot(data)
    assert list(figure.axes[0].lines[0].get_xdata()) == [1, 2, 3]
    values = figure.axes[0].lines[0].get_ydata()
    assert values[0] == 7 and isnan(values[1]) and values[2] == 5
    figure.clear()


def test_pair_plot_uses_requested_axes_and_population():
    data = prepared(request("pair"), two_columns([2, 4, 6]))
    figure = render_metric_plot(data)
    assert len(figure.axes) == 4
    assert figure.axes[1].collections[0].get_offsets().tolist() == [[2, 1], [4, 2], [6, 3]]
    assert sum(p.get_height() for p in figure.axes[0].patches) == 3
    figure.clear()


@pytest.mark.parametrize("kind", ["box", "correlation", "pair", "line"])
def test_new_plot_bundles_store_analysis_and_can_be_reproduced(kind, tmp_path):
    rows = (
        two_columns([2, 4, 6])
        if kind in {"correlation", "pair"}
        else [row([("passes.attempted", n)], match=n) for n in (1, 2, 3)]
    )
    data = prepared(request(kind), rows)
    image, metadata = write_plot_bundle(data, tmp_path / kind)
    assert image.read_bytes().startswith(b"\x89PNG")
    content = json.loads(metadata.read_text())
    assert "analysis" in content
    assert content["data"]["request"]["plot_version"] == "1.3.0"
    assert "NaN" not in metadata.read_text()


@pytest.mark.parametrize(
    "changes",
    [
        {"additional_metrics": ()},
        {"additional_metrics": ("passes.attempted",)},
        {"plot_version": "1.2.0"},
        {"kind": "bar", "additional_metrics": ("shots.total",)},
        {"metric_labels": {"not_selected": "Wrong label"}},
    ],
)
def test_invalid_multivariate_selections_are_rejected(changes):
    with pytest.raises(ValueError):
        request(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"aggregation": "selected_matches"},
        {"team_ids": ()},
        {"x_metric": "shots.total"},
    ],
)
def test_lines_require_explicit_entities_and_per_match_values(changes):
    with pytest.raises(ValueError):
        request("line", **changes)


def test_player_line_accepts_explicit_series_color():
    config = request(
        "line",
        group_by="player",
        team_ids=(),
        player_ids=("101",),
        style={"series_colors": {"101": "#ff0000"}},
    )
    rows = [observation([value(number=n)], match=n) for n in (1, 2, 3)]
    figure = render_metric_plot(prepared(config, rows))
    assert figure.axes[0].lines[0].get_color() == "#ff0000"
    figure.clear()
