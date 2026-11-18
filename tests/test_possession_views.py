from decimal import Decimal

import pytest

from football_analytics.ingestion.possession_time import (
    PossessionTimeInterval,
    PossessionTimeResult,
    TeamPossessionTime,
)
from football_analytics.ingestion.possession_views import (
    ReviewedContestWindow,
    build_possession_views,
)
from football_analytics.integrations.base import MatchEventContext


def sample():
    intervals = tuple(
        PossessionTimeInterval(
            period=1,
            start_ms=Decimal(start),
            end_ms=Decimal(end),
            classification=kind,
            external_team_id=team,
            evidence_event_ids=(),
            reason="fixture",
        )
        for start, end, kind, team in (
            (0, 24000, "attributed", "a"),
            (24000, 45000, "attributed", "b"),
            (45000, 90000, "unknown", None),
            (90000, 100000, "stopped", None),
        )
    )
    estimate = PossessionTimeResult(
        provider="statsbomb",
        external_match_id="1",
        events_sha256="a" * 64,
        max_gap_ms=5000,
        periods=(1,),
        elapsed_ms=Decimal(100000),
        attributed_ms=Decimal(45000),
        bounded_stoppage_ms=Decimal(10000),
        unknown_ms=Decimal(45000),
        attribution_coverage_pct=Decimal(45),
        teams=(
            TeamPossessionTime(
                external_team_id="a",
                estimated_ms=Decimal(24000),
                share_of_attributed_pct=Decimal("53.33"),
            ),
            TeamPossessionTime(
                external_team_id="b",
                estimated_ms=Decimal(21000),
                share_of_attributed_pct=Decimal("46.67"),
            ),
        ),
        intervals=intervals,
    )
    event = MatchEventContext(
        provider="statsbomb",
        external_match_id="1",
        external_event_id="duel-1",
        source_index=1,
        period=1,
        timestamp_ms=50000,
        event_type="Duel",
        external_team_id="a",
    )
    window = ReviewedContestWindow(
        provider="statsbomb",
        external_match_id="1",
        events_sha256="a" * 64,
        period=1,
        start_ms=Decimal(45000),
        end_ms=Decimal(60000),
        evidence_event_ids=("duel-1",),
        review_reference="synthetic reviewed fixture",
    )
    return estimate, event, window


def test_two_views_use_different_denominators_and_preserve_unknown():
    estimate, event, window = sample()
    report = build_possession_views(estimate, contests=(window,), contexts=(event,))
    assert [r.duration_ms for r in report.time_distribution] == [24000, 21000, 15000, 10000, 30000]
    assert [r.share_of_elapsed_pct for r in report.time_distribution] == [24, 21, 15, 10, 30]
    assert [r.share_of_controlled_pct for r in report.team_possession] == [
        Decimal("53.33"),
        Decimal("46.67"),
    ]
    assert report.controlled_coverage_pct == 45
    assert report.classified_coverage_pct == 70
    assert report.contest_assessment == "reviewed_windows_only"
    assert report.reviewed_contests == (window,)


def test_no_review_does_not_convert_unknown_into_contest():
    report = build_possession_views(sample()[0])
    assert report.time_distribution[2].duration_ms == 0
    assert report.time_distribution[-1].duration_ms == 45000
    assert report.contest_assessment == "not_reviewed"
    assert report.classified_coverage_pct == 55


def test_contest_does_not_change_team_possession_percentages():
    estimate, event, window = sample()
    before = build_possession_views(estimate)
    after = build_possession_views(estimate, contests=(window,), contexts=(event,))
    assert before.team_possession == after.team_possession
    assert before.controlled_ms == after.controlled_ms


def test_reviewed_window_splits_unknown_intervals_without_time_loss():
    estimate, event, window = sample()
    report = build_possession_views(estimate, contests=(window,), contexts=(event,))
    assert sum(i.end_ms - i.start_ms for i in report.intervals) == estimate.elapsed_ms
    assert all(
        a.end_ms == b.start_ms for a, b in zip(report.intervals, report.intervals[1:], strict=False)
    )
    assert next(i for i in report.intervals if i.category == "in_contest").evidence_event_ids == (
        "duel-1",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"events_sha256": "b" * 64},
        {"external_match_id": "2"},
        {"period": 2},
        {"start_ms": Decimal(20000)},
        {"end_ms": Decimal(95000)},
    ],
)
def test_conflicting_source_period_or_known_time_is_rejected(changes):
    estimate, event, window = sample()
    with pytest.raises(ValueError):
        build_possession_views(
            estimate, contests=(window.model_copy(update=changes),), contexts=(event,)
        )


def test_overlapping_contest_windows_are_rejected():
    estimate, event, window = sample()
    with pytest.raises(ValueError, match="overlap"):
        build_possession_views(estimate, contests=(window, window), contexts=(event,))


@pytest.mark.parametrize(
    "changes",
    [
        {"event_type": "Pressure"},
        {"period": 2},
        {"timestamp_ms": 1000},
        {"provider": "other"},
        {"external_event_id": "missing"},
    ],
)
def test_inadequate_contest_evidence_is_rejected(changes):
    estimate, event, window = sample()
    with pytest.raises(ValueError):
        build_possession_views(
            estimate, contests=(window,), contexts=(event.model_copy(update=changes),)
        )


def test_no_controlled_time_means_null_team_shares():
    estimate, _, _ = sample()
    empty = estimate.model_copy(
        update={
            "attributed_ms": Decimal(0),
            "bounded_stoppage_ms": Decimal(0),
            "unknown_ms": Decimal(100000),
            "teams": tuple(
                t.model_copy(update={"estimated_ms": Decimal(0), "share_of_attributed_pct": None})
                for t in estimate.teams
            ),
            "intervals": (
                PossessionTimeInterval(
                    period=1,
                    start_ms=Decimal(0),
                    end_ms=Decimal(100000),
                    classification="unknown",
                    external_team_id=None,
                    evidence_event_ids=(),
                    reason="missing",
                ),
            ),
        }
    )
    report = build_possession_views(empty)
    assert all(t.share_of_controlled_pct is None for t in report.team_possession)
    assert report.time_distribution[-1].share_of_elapsed_pct == 100


def test_inconsistent_partition_is_rejected():
    estimate, _, _ = sample()
    with pytest.raises(ValueError, match="totals"):
        build_possession_views(estimate.model_copy(update={"unknown_ms": Decimal(1)}))
    with pytest.raises(ValueError, match="gaps"):
        build_possession_views(estimate.model_copy(update={"intervals": estimate.intervals[1:]}))


@pytest.mark.parametrize(
    "changes",
    [
        {"end_ms": Decimal(45000)},
        {"review_reference": " "},
        {"evidence_event_ids": ()},
        {"evidence_event_ids": ("duel-1", "duel-1")},
    ],
)
def test_invalid_review_contract_is_rejected(changes):
    _, _, window = sample()
    with pytest.raises(ValueError):
        ReviewedContestWindow.model_validate(window.model_dump() | changes)
