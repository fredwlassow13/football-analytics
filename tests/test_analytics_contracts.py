import importlib
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from football_analytics.analytics.catalogue import METRICS, get_metric
from football_analytics.analytics.contracts import (
    Coverage,
    MetricQuery,
    MetricValue,
    QueryResult,
    ResultRow,
    SourceReference,
)


def query(**changes):
    return MetricQuery(
        **(dict(match_ids=(1,), group_by="team", metrics=("passes.attempted",)) | changes)
    )


def value(**changes):
    return MetricValue(
        **(
            dict(
                metric_id="passes.attempted",
                unit="count",
                status="available",
                value=0,
                coverage=Coverage(
                    source_complete=True,
                    population="presence_confirmed",
                    eligible_events=0,
                    covered_events=0,
                ),
            )
            | changes
        )
    )


def source(**changes):
    return SourceReference(
        **(
            dict(
                match_id=1,
                events_payload_id=uuid4(),
                events_sha256="a" * 64,
                available_periods=(1, 2),
            )
            | changes
        )
    )


def result(**changes):
    return QueryResult(
        **(
            dict(
                query=query(),
                sources=(source(),),
                rows=(ResultRow(match_ids=(1,), team_id="10", values=(value(),)),),
            )
            | changes
        )
    )


def test_catalogue_unique_and_implementation_references_resolve():
    assert len(METRICS) == len({m.id for m in METRICS})
    assert len(METRICS) > 24
    assert all(m.execution_available for m in METRICS)
    root = Path(__file__).resolve().parents[1]
    for metric in METRICS:
        for test in metric.tests:
            assert (root / test).is_file(), test
        if metric.family == "participation":
            module, member = metric.implementation.rsplit(".", 1)
            assert callable(getattr(importlib.import_module(module), member))
        else:
            module, cls, member = metric.implementation.rsplit(".", 2)
            model = getattr(importlib.import_module(module), cls)
            assert member in model.model_fields or hasattr(model, member)


def test_query_json_roundtrip_and_no_execution():
    q = MetricQuery.model_validate_json(
        json.dumps(
            {
                "match_ids": [1, 2],
                "group_by": "player",
                "metrics": ["shots.xg"],
                "periods": [1, 2],
            }
        )
    )
    assert q.match_ids == (1, 2)
    assert MetricQuery.model_validate_json(q.model_dump_json()) == q
    assert get_metric("passes.completion_pct").aggregation == "ratio_of_sums"
    assert "unknown" in get_metric("passes.completion_pct").denominator


@pytest.mark.parametrize(
    "changes",
    [
        {"match_ids": ()},
        {"match_ids": (1, 1)},
        {"match_ids": (0,)},
        {"match_ids": (True,)},
        {"metrics": ()},
        {"metrics": ("not.real",)},
        {"metrics": ("shots.total", "shots.total")},
        {"periods": ()},
        {"periods": (5,)},
        {"periods": (1, 1)},
        {"periods": (True,)},
        {"player_ids": ("1",)},
        {"team_ids": (" ",)},
        {"team_ids": ("1", "1")},
        {"metrics": ("participation.elapsed_on_field_ms",)},
        {"provider": "other"},
        {"play_phase": "open_play"},
        {"per_90": True},
    ],
)
def test_invalid_or_unsupported_query(changes):
    with pytest.raises((ValidationError, ValueError)):
        query(**changes)


def test_zero_and_unknown_are_distinct():
    assert value().value == 0
    missing = value(
        value=None,
        status="unknown",
        reasons=("participation_unknown",),
        coverage=Coverage(
            source_complete=True, population="unknown", eligible_events=0, covered_events=0
        ),
    )
    assert missing.value is None
    with pytest.raises(ValidationError):
        value(coverage=missing.coverage)


@pytest.mark.parametrize(
    "changes",
    [
        {"unit": "milliseconds"},
        {"definition_version": "2.0.0"},
        {"value": -1},
        {"value": True},
        {"value": Decimal("0")},
        {"value": Decimal("NaN")},
        {"value": None},
        {"status": "unknown", "value": 0, "reasons": ("missing",)},
        {"status": "unknown", "value": None},
        {"status": "incomplete", "reasons": ("missing",)},
    ],
)
def test_inconsistent_values_rejected(changes):
    with pytest.raises(ValidationError):
        value(**changes)


def test_percentage_requires_usable_denominator():
    pct = dict(
        metric_id="passes.completion_pct",
        unit="percent",
        value=Decimal("50.00"),
        numerator=1,
        denominator=2,
    )
    assert value(**pct).value == Decimal("50.00")
    with pytest.raises(ValidationError):
        value(**(pct | {"denominator": 0}))
    with pytest.raises(ValidationError):
        value(**(pct | {"numerator": 3}))


def test_partial_xg_cannot_be_presented_as_complete_total():
    incomplete = Coverage(
        source_complete=True, population="observed", eligible_events=2, covered_events=1
    )
    common = dict(
        unit="expected_goals",
        value=Decimal("0.2"),
        status="incomplete",
        coverage=incomplete,
        reasons=("one_shot_missing_xg",),
    )
    assert value(metric_id="shots.xg_available", **common).value == Decimal("0.2")
    with pytest.raises(ValidationError):
        value(metric_id="shots.xg", **common)
    assert value(metric_id="shots.xg", **(common | {"value": None})).value is None


def test_coverage_cannot_cover_more_than_population():
    with pytest.raises(ValidationError):
        Coverage(source_complete=True, population="observed", eligible_events=1, covered_events=2)


def test_result_provenance_and_json_roundtrip():
    r = result()
    assert QueryResult.model_validate_json(r.model_dump_json()) == r
    with pytest.raises(ValidationError):
        result(sources=(source(match_id=2),))
    with pytest.raises(ValidationError):
        result(sources=(source(), source()))


def test_result_rows_must_match_query():
    with pytest.raises(ValidationError):
        result(query=query(group_by="player"))
    with pytest.raises(ValidationError):
        result(query=query(team_ids=("20",)))
    with pytest.raises(ValidationError):
        result(query=query(metrics=("shots.total",)))
    with pytest.raises(ValidationError):
        result(rows=(ResultRow(match_ids=(2,), team_id="10", values=(value(),)),))
    r = result()
    with pytest.raises(ValidationError):
        result(rows=r.rows + r.rows)


def test_participation_requires_source_calculation():
    q = query(group_by="player", metrics=("participation.elapsed_on_field_ms",))
    with pytest.raises(ValidationError, match="calculation provenance"):
        result(query=q, rows=())
    with pytest.raises(ValidationError, match="lineup provenance"):
        source(participation_calculation_id=uuid4())
    with pytest.raises(ValidationError):
        source(lineups_payload_id=uuid4())


def test_percentage_must_match_components():
    with pytest.raises(ValidationError, match="differs from its numerator"):
        value(
            metric_id="passes.completion_pct",
            unit="percent",
            value=Decimal("99"),
            numerator=1,
            denominator=2,
        )
    assert value(
        metric_id="passes.completion_pct",
        unit="percent",
        value=Decimal("33.33"),
        numerator=1,
        denominator=3,
    ).value == Decimal("33.33")


def test_period_filter_is_checked_against_each_source():
    with pytest.raises(ValidationError, match="periods are unavailable"):
        result(query=query(periods=(3,)))
    assert result(query=query(periods=(3,)), sources=(source(available_periods=(1, 2, 3, 4)),))
    with pytest.raises(ValidationError):
        source(available_periods=(1, 2, 5))


def test_aggregate_row_cannot_hide_missing_matches():
    q = query(match_ids=(1, 2), aggregation="selected_matches")
    with pytest.raises(ValidationError, match="full requested selection"):
        result(query=q, sources=(source(), source(match_id=2)))


def test_incomplete_source_cannot_publish_available_zero():
    with pytest.raises(ValidationError, match="complete coverage"):
        value(
            coverage=Coverage(
                source_complete=False,
                population="presence_confirmed",
                eligible_events=0,
                covered_events=0,
            )
        )


def test_unknown_fields_rejected_at_result_boundary():
    with pytest.raises(ValidationError):
        value(invented_field=True)
