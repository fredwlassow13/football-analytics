import pytest

from football_analytics.ingestion.possession_metadata import (
    PossessionMetadataResult,
    build_possession_metadata,
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


def build(
    contexts: list[MatchEventContext],
    items: list[MatchEventPlayMetadata],
) -> PossessionMetadataResult:
    return build_possession_metadata(
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


def test_preserves_duration_out_and_pattern_partitions() -> None:
    contexts = [event(1), event(2), event(3)]
    result = build(
        contexts,
        [
            metadata(
                contexts[0],
                duration_seconds=0,
                out=True,
                play_pattern="regular_play",
                external_play_pattern_id="1",
            ),
            metadata(
                contexts[1],
                duration_seconds=1.5,
                out=False,
                play_pattern="from_kick_off",
                external_play_pattern_id="9",
            ),
            metadata(contexts[2]),
        ],
    )

    item = result.fragment_metadata[0]
    assert item.event_count == 3
    assert item.duration_coverage.model_dump() == {
        "present": 2,
        "missing": 1,
        "zero": 1,
        "positive": 1,
    }
    assert item.out_counts.model_dump() == {"true": 1, "false": 1, "unknown": 1}

    patterns = {
        (pattern.external_play_pattern_id, pattern.play_pattern): pattern
        for pattern in item.play_patterns
    }
    assert patterns[(None, None)].external_event_ids == ("event-3",)
    assert patterns[("1", "regular_play")].external_event_ids == ("event-1",)
    assert patterns[("9", "from_kick_off")].external_event_ids == ("event-2",)
    assert sum(pattern.event_count for pattern in item.play_patterns) == 3


def test_joins_by_id_and_preserves_source_order() -> None:
    contexts = [event(1), event(2)]
    items = [
        metadata(
            context,
            play_pattern="regular_play",
            external_play_pattern_id="1",
        )
        for context in contexts
    ]

    result = build(contexts, list(reversed(items)))

    assert result.fragment_metadata[0].play_patterns[0].external_event_ids == ("event-1", "event-2")


def test_missing_pattern_is_not_other() -> None:
    contexts = [event(1), event(2)]
    result = build(
        contexts,
        [
            metadata(
                contexts[0],
                play_pattern="other",
                external_play_pattern_id="5",
            ),
            metadata(contexts[1]),
        ],
    )

    assert {
        (pattern.external_play_pattern_id, pattern.play_pattern)
        for pattern in result.fragment_metadata[0].play_patterns
    } == {(None, None), ("5", "other")}


def test_does_not_merge_different_source_pattern_ids() -> None:
    contexts = [event(1), event(2)]
    result = build(
        contexts,
        [
            metadata(
                contexts[0],
                play_pattern="regular_play",
                external_play_pattern_id="1",
            ),
            metadata(
                contexts[1],
                play_pattern="regular_play",
                external_play_pattern_id="99",
            ),
        ],
    )

    assert len(result.fragment_metadata[0].play_patterns) == 2


def test_preserves_clock_diagnostics_without_discarding_metadata() -> None:
    contexts = [
        event(1, timestamp_ms=900),
        event(2, timestamp_ms=100),
    ]
    result = build(
        contexts,
        [metadata(context, duration_seconds=1.0) for context in contexts],
    )

    assert result.fragments[0].observed_span_ms is None
    assert result.fragments[0].issues == ("timestamp_regression",)
    assert result.fragment_metadata[0].duration_coverage.present == 2
    assert len(result.issues) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timestamp_ms", 900),
        ("source_index", 7),
        ("event_type", "Shot"),
        ("external_team_id", "771"),
    ],
)
def test_rejects_different_headers(field: str, value: object) -> None:
    context = event(1)

    with pytest.raises(ValueError, match="headers differ"):
        build([context], [metadata(context, **{field: value})])


@pytest.mark.parametrize("field", ["external_event_id", "source_index"])
def test_rejects_duplicate_metadata_sources(field: str) -> None:
    contexts = [event(1), event(2)]
    first = metadata(contexts[0])
    second = metadata(contexts[1], **{field: getattr(first, field)})

    with pytest.raises(ValueError, match="Duplicate"):
        build(contexts, [first, second])


def test_requires_metadata_for_excluded_shootout_events_too() -> None:
    contexts = [event(1), event(2, period=5)]

    with pytest.raises(ValueError, match="coverage differs"):
        build(contexts, [metadata(contexts[0])])


def test_rejects_extra_metadata_event() -> None:
    context = event(1)

    with pytest.raises(ValueError, match="coverage differs"):
        build([context], [metadata(context), metadata(event(2))])


def test_excludes_shootout_from_fragment_composition() -> None:
    contexts = [event(1), event(2, period=5)]
    result = build(
        contexts,
        [
            metadata(contexts[0], duration_seconds=0),
            metadata(contexts[1], duration_seconds=99),
        ],
    )

    assert result.excluded_shootout_event_ids == ("event-2",)
    assert len(result.fragment_metadata) == 1
    assert result.fragment_metadata[0].duration_coverage.zero == 1
    assert result.fragment_metadata[0].duration_coverage.positive == 0


def test_empty_input_preserves_provenance() -> None:
    result = build([], [])

    assert result.fragments == ()
    assert result.fragment_metadata == ()
    assert result.events_sha256 == "a" * 64
    assert result.policy_version == "1.0.0"
    assert result.metadata_policy_version == "1.0.0"
