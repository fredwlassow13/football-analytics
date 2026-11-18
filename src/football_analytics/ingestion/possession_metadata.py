from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.possession_sequences import (
    PossessionSequenceResult,
    build_possession_sequences,
)
from football_analytics.integrations.base import (
    MatchEvent,
    MatchEventContext,
    MatchEventPlayMetadata,
)
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary


class EventDurationCoverage(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    present: int = Field(ge=0)
    missing: int = Field(ge=0)
    zero: int = Field(ge=0)
    positive: int = Field(ge=0)


class EventOutCounts(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    true: int = Field(ge=0)
    false: int = Field(ge=0)
    unknown: int = Field(ge=0)


class PossessionPlayPatternCount(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_play_pattern_id: str | None
    play_pattern: str | None
    event_count: int = Field(gt=0)
    external_event_ids: tuple[str, ...]


class PossessionFragmentMetadata(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    fragment_number: int = Field(gt=0)
    event_count: int = Field(gt=0)
    duration_coverage: EventDurationCoverage
    out_counts: EventOutCounts
    play_patterns: tuple[PossessionPlayPatternCount, ...]


class PossessionMetadataResult(PossessionSequenceResult):
    metadata_policy_version: Literal["1.0.0"] = "1.0.0"
    fragment_metadata: tuple[PossessionFragmentMetadata, ...]


def _summarize_fragment(
    fragment_number: int,
    records: Sequence[MatchEventPlayMetadata],
) -> PossessionFragmentMetadata:
    present = sum(record.duration_seconds is not None for record in records)
    zero = sum(record.duration_seconds == 0 for record in records)

    grouped: dict[
        tuple[str | None, str | None],
        list[MatchEventPlayMetadata],
    ] = defaultdict(list)

    for record in records:
        grouped[(record.external_play_pattern_id, record.play_pattern)].append(record)

    return PossessionFragmentMetadata(
        fragment_number=fragment_number,
        event_count=len(records),
        duration_coverage=EventDurationCoverage(
            present=present,
            missing=len(records) - present,
            zero=zero,
            positive=present - zero,
        ),
        out_counts=EventOutCounts(
            true=sum(record.out is True for record in records),
            false=sum(record.out is False for record in records),
            unknown=sum(record.out is None for record in records),
        ),
        play_patterns=tuple(
            PossessionPlayPatternCount(
                external_play_pattern_id=pattern_id,
                play_pattern=pattern_name,
                event_count=len(pattern_records),
                external_event_ids=tuple(record.external_event_id for record in pattern_records),
            )
            for (pattern_id, pattern_name), pattern_records in sorted(
                grouped.items(),
                key=lambda item: (item[0][0] or "", item[0][1] or ""),
            )
        ),
    )


def build_possession_metadata(
    contexts: Sequence[MatchEventContext],
    metadata: Sequence[MatchEventPlayMetadata],
    periods: Sequence[PeriodBoundary],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
) -> PossessionMetadataResult:
    sequences = build_possession_sequences(
        contexts,
        periods,
        provider=provider,
        match_id=match_id,
        events_sha256=events_sha256,
    )

    context_by_id = {context.external_event_id: context for context in contexts}
    metadata_by_id: dict[str, MatchEventPlayMetadata] = {}
    seen_indexes: set[int] = set()

    for item in metadata:
        if not isinstance(item, MatchEventPlayMetadata):
            raise ValueError("Unsupported play metadata event type")

        if (item.provider, item.external_match_id) != (provider, match_id):
            raise ValueError("Metadata differs from requested provider and match")

        if item.external_event_id in metadata_by_id or item.source_index in seen_indexes:
            raise ValueError("Duplicate play metadata source event")

        metadata_by_id[item.external_event_id] = item
        seen_indexes.add(item.source_index)

    if set(metadata_by_id) != set(context_by_id):
        raise ValueError("Context and play metadata coverage differs")

    header_fields = set(MatchEvent.model_fields)

    for context in contexts:
        item = metadata_by_id[context.external_event_id]
        if context.model_dump(include=header_fields) != item.model_dump(include=header_fields):
            raise ValueError(
                f"Context and play metadata headers differ: {context.external_event_id}"
            )

    fragment_metadata = tuple(
        _summarize_fragment(
            fragment.fragment_number,
            [metadata_by_id[event_id] for event_id in fragment.external_event_ids],
        )
        for fragment in sequences.fragments
    )

    return PossessionMetadataResult.model_validate(
        sequences.model_dump() | {"fragment_metadata": fragment_metadata}
    )
