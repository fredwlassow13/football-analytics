from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from test_match_export import export

from football_analytics.analytics.catalogue import get_metric
from football_analytics.analytics.contracts import Coverage, MetricValue
from football_analytics.analytics.match_export_batch import export_match_batch, write_export_index
from football_analytics.visualization.metric_plot_contracts import PlotObservation, PlotRequest
from football_analytics.visualization.metric_plot_data import (
    prepare_metric_plot,
    select_observations,
)


def value(
    metric="passes.attempted",
    number=0,
    *,
    status="available",
    reviews=(),
    numerator=None,
    denominator=None,
):
    return MetricValue(
        metric_id=metric,
        unit=get_metric(metric).unit,
        status=status,
        value=number,
        numerator=numerator,
        denominator=denominator,
        reasons=() if status == "available" else ("source_unknown",),
        review_reasons=reviews,
        coverage=Coverage(
            source_complete=status != "incomplete",
            population="unknown" if status == "unknown" else "observed",
            eligible_events=10,
            covered_events=10 if status != "incomplete" else 5,
        ),
    )


def observation(values, match=1, player="101"):
    return PlotObservation(
        match_ids=(match,),
        team_id="1",
        team_name="Team",
        player_id=player,
        label="Player",
        values=tuple(values),
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "scatter"},
        {"y_metric": "shots.total"},
        {"size_metric": "shots.total"},
        {"kind": "histogram", "top_n": 2},
        {"match_ids": (1, 1)},
        {"group_by": "team", "player_ids": ("1",)},
        {"x_metric": "not.registered"},
        {"style": {"color": "red"}},
        {"style": {"palette": ()}},
    ],
)
def test_invalid_plot_config_is_rejected(changes):
    with pytest.raises(ValueError):
        PlotRequest(**(dict(kind="bar", group_by="player", x_metric="passes.attempted") | changes))


def test_zero_is_preserved_and_unknown_is_excluded():
    request = PlotRequest(kind="bar", group_by="player", x_metric="passes.attempted")
    rows = [
        observation([value()], player="101"),
        observation([value(number=None, status="unknown")], player="102"),
    ]
    points, excluded, omitted = select_observations(rows, request)
    assert points[0].values[0].value == 0
    assert len(points) == len(excluded) == 1
    assert excluded[0].observation.values[0].value is None
    assert omitted == 0


def test_percentage_aggregation_uses_components():
    request = PlotRequest(
        kind="bar",
        group_by="player",
        x_metric="passes.completion_pct",
        aggregation="selected_matches",
    )
    rows = [
        observation([value("passes.completion_pct", Decimal("50.00"), numerator=1, denominator=2)]),
        observation(
            [value("passes.completion_pct", Decimal("12.50"), numerator=1, denominator=8)], match=2
        ),
    ]
    points, excluded, _ = select_observations(rows, request)
    assert not excluded
    assert points[0].values[0].value == Decimal("20.00")
    assert points[0].match_ids == (1, 2)


def test_unknown_match_is_not_removed_before_aggregation():
    request = PlotRequest(
        kind="bar", group_by="player", x_metric="passes.attempted", aggregation="selected_matches"
    )
    rows = [
        observation([value(number=4)]),
        observation([value(number=None, status="unknown")], match=2),
    ]
    points, excluded, _ = select_observations(rows, request)
    assert not points
    assert len(excluded) == 1
    assert excluded[0].observation.values[0].status == "unknown"


def test_review_policy_preserves_or_excludes_warning():
    request = PlotRequest(kind="bar", group_by="player", x_metric="passes.attempted")
    rows = [observation([value(number=2, reviews=("source_review",))])]
    assert len(select_observations(rows, request)[0]) == 1
    filtered = request.model_copy(update={"review_policy": "exclude"})
    assert len(select_observations(rows, filtered)[1]) == 1


def test_scatter_excludes_entire_pair_if_y_is_unknown():
    request = PlotRequest(
        kind="scatter", group_by="player", x_metric="passes.attempted", y_metric="shots.total"
    )
    row = observation([value(number=10), value("shots.total", None, status="unknown")])
    points, excluded, _ = select_observations([row], request)
    assert not points and len(excluded) == 1
    assert excluded[0].reasons == ("shots.total:unknown",)


def test_top_n_counts_omitted_observations_and_sorts_values():
    request = PlotRequest(kind="bar", group_by="player", x_metric="passes.attempted", top_n=1)
    rows = [observation([value(number=1)]), observation([value(number=8)], player="102")]
    points, excluded, omitted = select_observations(rows, request)
    assert points[0].player_id == "102"
    assert not excluded and omitted == 1


@pytest.fixture(scope="module")
def sample():
    return export()


def write_sample_index(sample, tmp_path):
    with (
        patch(
            "football_analytics.analytics.match_export_batch.load_query_sources",
            return_value=(sample.source, None, None),
        ),
        patch(
            "football_analytics.analytics.match_export_batch.build_match_export",
            return_value=sample,
        ),
    ):
        index = export_match_batch(MagicMock(), (123,), tmp_path)
    path = tmp_path / "index.json"
    write_export_index(index, path)
    return path


def test_export_loader_checks_hashes_and_preserves_exclusions(sample, tmp_path):
    path = write_sample_index(sample, tmp_path)
    request = PlotRequest(kind="bar", group_by="player", x_metric="passes.attempted")
    prepared = prepare_metric_plot(path, request)
    assert prepared.population_count == 24
    assert len(prepared.excluded) == 2
    assert prepared.sources[0].export_id == sample.export_id
    assert prepared.points
    data_path = tmp_path / "123.json"
    data_path.write_bytes(data_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash/size"):
        prepare_metric_plot(path, request)


def test_missing_match_is_rejected(sample, tmp_path):
    path = write_sample_index(sample, tmp_path)
    request = PlotRequest(
        kind="bar", group_by="team", x_metric="passes.attempted", match_ids=(999,)
    )
    with pytest.raises(ValueError, match="outside export index"):
        prepare_metric_plot(path, request)
