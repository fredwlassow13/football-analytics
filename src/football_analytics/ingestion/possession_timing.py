from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.possession_metadata import (
    build_possession_metadata,
)
from football_analytics.ingestion.possession_sequences import (
    PossessionIssueCode,
    PossessionSequenceIssue,
)
from football_analytics.integrations.base import (
    MatchEventContext,
    MatchEventPlayMetadata,
)
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary


class EventTimingObservation(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_event_id: str
    source_index: int = Field(gt=0)
    period: int = Field(ge=1, le=4)
    event_type: str
    fragment_number: int = Field(gt=0)
    start_timestamp_ms: int = Field(ge=0)
    duration_ms: Decimal = Field(ge=0)
    calculated_end_timestamp_ms: Decimal = Field(ge=0)
    out: bool | None
    next_event_id: str | None
    next_event_timestamp_ms: int | None
    delta_to_next_event_ms: Decimal | None
    next_fragment_number: int | None
    next_fragment_first_timestamp_ms: int | None
    delta_to_next_fragment_ms: Decimal | None
    delta_to_period_end_ms: Decimal
    fragment_issues: tuple[PossessionIssueCode, ...]


class PossessionTimingAudit(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: Literal["1.0.0"] = "1.0.0"
    sequence_policy_version: Literal["1.0.0"] = "1.0.0"
    metadata_policy_version: Literal["1.0.0"] = "1.0.0"
    available_periods: tuple[int, ...]
    observations: tuple[EventTimingObservation, ...]
    missing_duration_event_ids: tuple[str, ...]
    external_event_ids: tuple[str, ...]
    excluded_shootout_event_ids: tuple[str, ...]
    source_issues: tuple[PossessionSequenceIssue, ...]


def audit_possession_timing(
    contexts: Sequence[MatchEventContext],
    metadata: Sequence[MatchEventPlayMetadata],
    periods: Sequence[PeriodBoundary],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
) -> PossessionTimingAudit:
    analysis = build_possession_metadata(
        contexts,
        metadata,
        periods,
        provider=provider,
        match_id=match_id,
        events_sha256=events_sha256,
    )

    contexts_by_id = {context.external_event_id: context for context in contexts}
    metadata_by_id = {item.external_event_id: item for item in metadata}
    clock_by_period = {period.period: period for period in periods}
    playing = [contexts_by_id[event_id] for event_id in analysis.external_event_ids]
    fragment_by_event = {
        event_id: fragment
        for fragment in analysis.fragments
        for event_id in fragment.external_event_ids
    }

    next_event_by_id = {
        left_event.external_event_id: right_event
        for left_event, right_event in zip(playing, playing[1:], strict=False)
        if left_event.period == right_event.period
    }
    next_fragment_by_number = {
        left_fragment.fragment_number: right_fragment
        for left_fragment, right_fragment in zip(
            analysis.fragments, analysis.fragments[1:], strict=False
        )
        if left_fragment.period == right_fragment.period
    }

    observations: list[EventTimingObservation] = []
    missing_duration_ids: list[str] = []

    for context in playing:
        item = metadata_by_id[context.external_event_id]
        duration = item.duration_seconds

        if duration is None:
            missing_duration_ids.append(context.external_event_id)
            continue

        fragment = fragment_by_event[context.external_event_id]
        following_event = next_event_by_id.get(context.external_event_id)
        following_fragment = next_fragment_by_number.get(fragment.fragment_number)

        duration_ms = Decimal(str(duration)) * Decimal(1000)
        calculated_end = Decimal(context.timestamp_ms) + duration_ms

        observations.append(
            EventTimingObservation(
                external_event_id=context.external_event_id,
                source_index=context.source_index,
                period=context.period,
                event_type=context.event_type,
                fragment_number=fragment.fragment_number,
                start_timestamp_ms=context.timestamp_ms,
                duration_ms=duration_ms,
                calculated_end_timestamp_ms=calculated_end,
                out=item.out,
                next_event_id=(
                    following_event.external_event_id if following_event is not None else None
                ),
                next_event_timestamp_ms=(
                    following_event.timestamp_ms if following_event is not None else None
                ),
                delta_to_next_event_ms=(
                    Decimal(following_event.timestamp_ms) - calculated_end
                    if following_event is not None
                    else None
                ),
                next_fragment_number=(
                    following_fragment.fragment_number if following_fragment is not None else None
                ),
                next_fragment_first_timestamp_ms=(
                    following_fragment.first_timestamp_ms
                    if following_fragment is not None
                    else None
                ),
                delta_to_next_fragment_ms=(
                    Decimal(following_fragment.first_timestamp_ms) - calculated_end
                    if following_fragment is not None
                    else None
                ),
                delta_to_period_end_ms=(
                    Decimal(clock_by_period[context.period].duration_ms) - calculated_end
                ),
                fragment_issues=fragment.issues,
            )
        )

    return PossessionTimingAudit(
        provider=provider,
        external_match_id=match_id,
        events_sha256=events_sha256,
        sequence_policy_version=analysis.policy_version,
        metadata_policy_version=analysis.metadata_policy_version,
        available_periods=analysis.available_periods,
        observations=tuple(observations),
        missing_duration_event_ids=tuple(missing_duration_ids),
        external_event_ids=analysis.external_event_ids,
        excluded_shootout_event_ids=analysis.excluded_shootout_event_ids,
        source_issues=analysis.issues,
    )
