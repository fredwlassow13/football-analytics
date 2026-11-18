from decimal import Decimal

from football_analytics.ingestion.possession_timing import (
    PossessionTimingAudit,
    audit_possession_timing,
)
from football_analytics.integrations.base import (
    MatchEvent,
    MatchEventContext,
    MatchEventPlayMetadata,
)
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary


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


def metadata(
    context: MatchEventContext,
    **changes: object,
) -> MatchEventPlayMetadata:
    return MatchEventPlayMetadata.model_validate(
        context.model_dump(include=set(MatchEvent.model_fields)) | changes
    )


def audit(
    contexts: list[MatchEventContext],
    items: list[MatchEventPlayMetadata],
) -> PossessionTimingAudit:
    return audit_possession_timing(
        contexts,
        items,
        [
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
        ],
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
    )


def test_preserves_submillisecond_difference_without_rounding() -> None:
    contexts = [event(1), event(2, possession_sequence=2)]
    result = audit(
        contexts,
        [
            metadata(contexts[0], duration_seconds=0.100001),
            metadata(contexts[1]),
        ],
    )

    observation = result.observations[0]
    assert observation.duration_ms == Decimal("100.001")
    assert observation.calculated_end_timestamp_ms == Decimal("200.001")
    assert observation.delta_to_next_event_ms == Decimal("-0.001")
    assert observation.delta_to_next_fragment_ms == Decimal("-0.001")

    exported = result.model_dump(mode="json")
    value = exported["observations"][0]["delta_to_next_event_ms"]
    assert isinstance(value, str)
    assert Decimal(value) == Decimal("-0.001")


def test_preserves_overlap_without_creating_source_error() -> None:
    contexts = [event(1), event(2, possession_sequence=2)]
    result = audit(
        contexts,
        [
            metadata(contexts[0], duration_seconds=0.25, out=True),
            metadata(contexts[1]),
        ],
    )

    observation = result.observations[0]
    assert observation.calculated_end_timestamp_ms == Decimal(350)
    assert observation.delta_to_next_event_ms == Decimal(-150)
    assert observation.delta_to_next_fragment_ms == Decimal(-150)
    assert observation.delta_to_period_end_ms == Decimal(650)
    assert observation.out is True
    assert observation.fragment_issues == ()
    assert result.source_issues == ()


def test_preserves_calculated_end_after_period_without_clipping() -> None:
    context = event(1)
    result = audit(
        [context],
        [metadata(context, duration_seconds=2.0)],
    )

    observation = result.observations[0]
    assert observation.calculated_end_timestamp_ms == Decimal(2100)
    assert observation.delta_to_period_end_ms == Decimal(-1100)
    assert observation.next_event_id is None
    assert observation.next_fragment_number is None


def test_distinguishes_missing_and_zero_duration() -> None:
    contexts = [event(1), event(2)]
    result = audit(
        contexts,
        [
            metadata(contexts[0]),
            metadata(contexts[1], duration_seconds=0),
        ],
    )

    assert result.missing_duration_event_ids == ("event-1",)
    assert len(result.observations) == 1
    assert result.observations[0].external_event_id == "event-2"
    assert result.observations[0].duration_ms == Decimal(0)
    assert result.observations[0].calculated_end_timestamp_ms == Decimal(200)
    assert result.observations[0].out is None
    assert result.external_event_ids == ("event-1", "event-2")


def test_does_not_compare_across_periods() -> None:
    contexts = [event(1), event(2, period=2, timestamp_ms=0)]
    result = audit(
        contexts,
        [
            metadata(contexts[0], duration_seconds=0.1),
            metadata(contexts[1]),
        ],
    )

    observation = result.observations[0]
    assert observation.next_event_id is None
    assert observation.delta_to_next_event_ms is None
    assert observation.next_fragment_number is None
    assert observation.delta_to_next_fragment_ms is None


def test_preserves_source_regression_and_event_order() -> None:
    contexts = [
        event(1, timestamp_ms=900),
        event(2, timestamp_ms=100),
    ]
    result = audit(
        contexts,
        [metadata(context, duration_seconds=0) for context in contexts],
    )

    assert [item.external_event_id for item in result.observations] == ["event-1", "event-2"]
    assert result.observations[0].delta_to_next_event_ms == Decimal(-800)
    assert all(item.fragment_issues == ("timestamp_regression",) for item in result.observations)
    assert len(result.source_issues) == 1


def test_keeps_shootout_out_of_timing_audit() -> None:
    contexts = [event(1), event(2, period=5)]
    result = audit(
        contexts,
        [metadata(context, duration_seconds=0) for context in contexts],
    )

    assert len(result.observations) == 1
    assert result.external_event_ids == ("event-1",)
    assert result.excluded_shootout_event_ids == ("event-2",)


def test_empty_input_preserves_provenance() -> None:
    result = audit([], [])

    assert result.observations == ()
    assert result.missing_duration_event_ids == ()
    assert result.events_sha256 == "a" * 64
    assert result.policy_version == "1.0.0"
    assert result.sequence_policy_version == "1.0.0"
    assert result.metadata_policy_version == "1.0.0"
