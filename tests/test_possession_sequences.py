import pytest

from football_analytics.ingestion.possession_sequences import (
    PossessionSequenceResult,
    build_possession_sequences,
)
from football_analytics.integrations.base import MatchEventContext
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary


def clock() -> list[PeriodBoundary]:
    return [
        PeriodBoundary(
            period=1,
            duration_ms=1000,
            start_elapsed_ms=0,
            end_elapsed_ms=1000,
        ),
        PeriodBoundary(
            period=2,
            duration_ms=1000,
            start_elapsed_ms=1000,
            end_elapsed_ms=2000,
        ),
    ]


def event(index: int, **changes: object) -> MatchEventContext:
    return MatchEventContext.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_event_id": f"event-{index}",
            "source_index": index,
            "period": 1,
            "timestamp_ms": index * 100,
            "event_type": "Pass",
            "external_team_id": "779",
            "external_player_id": "player-1",
            "possession_sequence": 1,
            "external_possession_team_id": "779",
        }
        | changes
    )


def build(events: list[MatchEventContext]) -> PossessionSequenceResult:
    return build_possession_sequences(
        events,
        clock(),
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
    )


def test_preserves_source_order_and_label_boundaries() -> None:
    result = build(
        [
            event(3, possession_sequence=2),
            event(1),
            event(2),
        ]
    )

    assert len(result.fragments) == 2
    first = result.fragments[0]
    assert first.external_event_ids == ("event-1", "event-2")
    assert first.first_source_index == 1
    assert first.last_source_index == 2
    assert first.observed_span_ms == 100
    assert first.event_count == 2
    assert result.external_event_ids == ("event-1", "event-2", "event-3")
    assert result.issues == ()


def test_opponent_action_does_not_change_possession_owner() -> None:
    result = build(
        [
            event(1),
            event(2, event_type="Pressure", external_team_id="771"),
        ]
    )

    assert len(result.fragments) == 1
    assert result.fragments[0].external_possession_team_id == "779"
    assert result.fragments[0].event_type_counts == {"Pass": 1, "Pressure": 1}


def test_preserves_timestamp_regression_without_reordering() -> None:
    result = build(
        [
            event(1, timestamp_ms=900),
            event(2, event_type="Ball Receipt*", timestamp_ms=100),
        ]
    )

    fragment = result.fragments[0]
    assert fragment.external_event_ids == ("event-1", "event-2")
    assert fragment.first_timestamp_ms == 900
    assert fragment.last_timestamp_ms == 100
    assert fragment.observed_span_ms is None
    assert fragment.issues == ("timestamp_regression",)


def test_cross_fragment_regression_marks_both_fragments() -> None:
    result = build(
        [
            event(1, timestamp_ms=900),
            event(2, possession_sequence=2, timestamp_ms=100),
        ]
    )

    assert len(result.fragments) == 2
    assert all(fragment.observed_span_ms is None for fragment in result.fragments)


def test_does_not_bridge_missing_possession_label() -> None:
    result = build(
        [
            event(1),
            event(2, possession_sequence=None),
            event(3),
        ]
    )

    assert len(result.fragments) == 3
    assert result.fragments[1].possession_sequence is None
    assert result.fragments[1].external_possession_team_id is None
    assert {issue.code for issue in result.issues} == {
        "missing_possession_sequence",
        "non_contiguous_label",
    }


def test_does_not_infer_missing_possession_team() -> None:
    result = build(
        [
            event(1),
            event(2, external_possession_team_id=None),
        ]
    )

    fragment = result.fragments[0]
    assert fragment.external_possession_team_id is None
    assert fragment.observed_possession_team_ids == ("779",)
    assert "missing_possession_team" in fragment.issues


def test_reports_conflicting_owners_without_splitting_same_label() -> None:
    result = build(
        [
            event(1),
            event(2, external_possession_team_id="771"),
        ]
    )

    assert len(result.fragments) == 1
    fragment = result.fragments[0]
    assert fragment.external_possession_team_id is None
    assert fragment.observed_possession_team_ids == ("771", "779")
    assert "conflicting_possession_teams" in fragment.issues


def test_does_not_merge_recurrent_label() -> None:
    result = build(
        [
            event(1),
            event(2, possession_sequence=2),
            event(3),
        ]
    )

    assert len(result.fragments) == 3
    assert "non_contiguous_label" in result.fragments[0].issues
    assert "non_contiguous_label" in result.fragments[2].issues


def test_source_gap_splits_fragment_and_remains_visible() -> None:
    result = build([event(1), event(3)])

    assert len(result.fragments) == 2
    assert {issue.code for issue in result.issues} == {"source_index_gap", "non_contiguous_label"}


def test_period_boundary_splits_same_label() -> None:
    result = build([event(1), event(2, period=2, timestamp_ms=0)])

    assert len(result.fragments) == 2
    assert result.fragments[1].period == 2
    assert not any(issue.code == "non_contiguous_label" for issue in result.issues)


def test_reports_event_after_period_end() -> None:
    result = build([event(1, timestamp_ms=1001)])

    assert result.fragments[0].observed_span_ms is None
    assert result.issues[0].code == "timestamp_after_period_end"


def test_accepts_timestamp_exactly_at_period_end() -> None:
    result = build([event(1, timestamp_ms=1000)])

    assert result.fragments[0].observed_span_ms == 0
    assert result.issues == ()


def test_reports_period_order_regression() -> None:
    result = build(
        [
            event(1, period=2),
            event(2, period=1),
        ]
    )

    assert any(issue.code == "period_order_regression" for issue in result.issues)


def test_excludes_shootout_without_losing_ids() -> None:
    result = build([event(1), event(2, period=5)])

    assert result.external_event_ids == ("event-1",)
    assert result.excluded_shootout_event_ids == ("event-2",)
    assert len(result.fragments) == 1


@pytest.mark.parametrize("field", ["external_event_id", "source_index"])
def test_rejects_duplicate_source_headers(field: str) -> None:
    first = event(1)
    second = event(2, **{field: getattr(first, field)})

    with pytest.raises(ValueError, match="Duplicate"):
        build([first, second])


@pytest.mark.parametrize("field", ["provider", "external_match_id"])
def test_rejects_source_identity_mismatch(field: str) -> None:
    with pytest.raises(ValueError, match="requested provider and match"):
        build([event(1, **{field: "other"})])


def test_rejects_unknown_playing_period() -> None:
    with pytest.raises(ValueError, match="incompatible"):
        build([event(1, period=3)])


def test_rejects_invalid_clock_boundaries() -> None:
    boundaries = clock()
    boundaries[1] = PeriodBoundary(
        period=2,
        duration_ms=1000,
        start_elapsed_ms=1001,
        end_elapsed_ms=2001,
    )

    with pytest.raises(ValueError, match="noncontiguous"):
        build_possession_sequences(
            [],
            boundaries,
            provider="statsbomb",
            match_id="3869685",
            events_sha256="a" * 64,
        )


def test_empty_events_preserve_provenance() -> None:
    result = build([])

    assert result.fragments == ()
    assert result.issues == ()
    assert result.events_sha256 == "a" * 64
    assert result.available_periods == (1, 2)
