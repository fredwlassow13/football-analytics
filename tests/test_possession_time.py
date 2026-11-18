from decimal import Decimal

import pytest
from test_attack_sequences import build
from test_possession_metadata import event

from football_analytics.ingestion.possession_time import estimate_possession_time
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary

CLOCK = [
    PeriodBoundary(
        period=p, duration_ms=1000, start_elapsed_ms=(p - 1) * 1000, end_elapsed_ms=p * 1000
    )
    for p in (1, 2)
]


def estimate(rows, *, overrides=None, **options):
    contexts = [event(i + 1, **row) for i, row in enumerate(rows)]
    attacks = build(
        rows, overrides={-1: {"pass_type": "Kick Off"}} if overrides is None else overrides
    )
    result = estimate_possession_time(contexts, attacks, CLOCK, team_ids=("779", "771"), **options)
    return result


def test_short_gap_is_estimated_but_period_edges_stay_unknown():
    result = estimate([{}, {}, {"event_type": "Half End"}])
    assert result.elapsed_ms == 2000
    assert result.attributed_ms == 100
    assert result.unknown_ms == 1900
    assert result.attribution_coverage_pct == Decimal("5.00")
    assert result.teams[0].share_of_attributed_pct == Decimal("100.00")
    assert result.teams[1].share_of_attributed_pct == Decimal("0.00")
    assert result.intervals[1].evidence_event_ids == ("event-1", "event-2")


def test_two_teams_use_attributed_time_denominator_not_elapsed_time():
    away = {
        "external_team_id": "771",
        "external_possession_team_id": "771",
        "possession_sequence": 2,
    }
    result = estimate([{}, {}, away, away, {"event_type": "Half End", **away}])
    assert result.attributed_ms == 200
    assert [t.estimated_ms for t in result.teams] == [100, 100]
    assert [t.share_of_attributed_pct for t in result.teams] == [50, 50]
    assert result.attribution_coverage_pct == 10


def test_gap_cap_excludes_long_interval_without_truncating_it():
    result = estimate([{}, {}, {"event_type": "Half End"}], max_gap_ms=50)
    assert result.attributed_ms == 0
    assert result.unknown_ms == 2000
    assert all(t.share_of_attributed_pct is None for t in result.teams)


def test_bounded_stoppage_is_separate_and_fractional_ms_are_preserved():
    result = estimate(
        [{}, {}, {}, {"event_type": "Half End"}],
        overrides={
            -1: {"pass_type": "Kick Off"},
            2: {"out": True, "duration_seconds": 0.0005},
            -3: {"pass_type": "Throw-in"},
        },
    )
    assert result.attributed_ms == 100
    assert result.bounded_stoppage_ms == Decimal("99.5")
    assert result.unknown_ms == Decimal("1800.5")
    assert result.attributed_ms + result.bounded_stoppage_ms + result.unknown_ms == 2000


def test_missing_stop_time_does_not_become_known_stoppage():
    result = estimate(
        [{}, {}, {}, {"event_type": "Half End"}],
        overrides={
            -1: {"pass_type": "Kick Off"},
            2: {"out": True},
            -3: {"pass_type": "Throw-in"},
        },
    )
    assert result.bounded_stoppage_ms == 0
    assert result.unknown_ms > 0


def test_review_attack_does_not_support_attribution():
    result = estimate([{}, {}, {"event_type": "Half End"}], overrides={})
    assert result.attributed_ms == 0
    assert result.unknown_ms == 2000


def test_clock_regression_is_not_sorted_or_converted_to_negative_time():
    result = estimate([{"timestamp_ms": 200}, {"timestamp_ms": 100}, {"event_type": "Half End"}])
    assert result.attributed_ms == 0
    assert result.unknown_ms == 2000


def test_admin_event_between_controls_prevents_bridging():
    result = estimate([{}, {"event_type": "Tactical Shift"}, {}, {"event_type": "Half End"}])
    assert result.attributed_ms == 0


def test_same_timestamp_does_not_create_duration():
    result = estimate([{}, {"timestamp_ms": 100}, {"event_type": "Half End"}])
    assert result.attributed_ms == 0


def test_empty_period_is_unknown_and_selection_changes_denominator():
    result = estimate([{}, {}, {"event_type": "Half End"}], selected_periods=(2,))
    assert result.elapsed_ms == result.unknown_ms == 1000
    assert result.attributed_ms == 0
    assert result.attribution_coverage_pct == 0


def test_shootout_is_excluded_from_duration():
    result = estimate([{}, {}, {"event_type": "Half End"}, {"period": 5}])
    assert result.elapsed_ms == 2000
    assert result.attributed_ms == 100


@pytest.mark.parametrize(
    "options",
    [
        {"max_gap_ms": 0},
        {"max_gap_ms": True},
        {"selected_periods": ()},
        {"selected_periods": (1, 1)},
        {"selected_periods": (5,)},
        {"selected_periods": (True,)},
    ],
)
def test_invalid_selection_is_rejected(options):
    with pytest.raises(ValueError):
        estimate([{}, {}], **options)


def test_duplicate_or_different_source_is_rejected():
    contexts = [event(1), event(2)]
    attacks = build([{}, {}])
    for invalid in (
        [contexts[0], contexts[0]],
        [contexts[0].model_copy(update={"provider": "other"}), contexts[1]],
    ):
        with pytest.raises(ValueError):
            estimate_possession_time(invalid, attacks, CLOCK, team_ids=("779", "771"))


def test_intervals_partition_each_period_without_overlap():
    result = estimate([{}, {}, {}, {"event_type": "Half End"}])
    for period in (1, 2):
        intervals = [i for i in result.intervals if i.period == period]
        assert intervals[0].start_ms == 0
        assert intervals[-1].end_ms == 1000
        assert all(a.end_ms == b.start_ms for a, b in zip(intervals, intervals[1:], strict=False))
    assert sum((i.end_ms - i.start_ms for i in result.intervals), Decimal(0)) == 2000
