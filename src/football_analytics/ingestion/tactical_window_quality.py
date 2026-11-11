from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.tactical_window_catalogue import TacticalWindow
from football_analytics.integrations.base import MatchEvent
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary


class EventClockIssue(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    code: Literal["event_clock_outside_source_window"] = "event_clock_outside_source_window"
    provider: str
    external_match_id: str
    external_event_id: str
    event_type: str
    source_index: int = Field(gt=0)
    period: int = Field(ge=1, le=4)
    window_number: int = Field(gt=0)
    reported_elapsed_ms: int = Field(ge=0)
    window_start_elapsed_ms: int = Field(ge=0)
    window_end_elapsed_ms: int = Field(ge=0)
    window_start_source_index: int = Field(gt=0)
    window_end_source_index: int = Field(gt=0)


def audit_event_clocks(
    events: Sequence[MatchEvent],
    windows: Sequence[TacticalWindow],
    periods: Sequence[PeriodBoundary],
) -> list[EventClockIssue]:
    identities = {(window.provider, window.external_match_id) for window in windows}

    if len(identities) != 1:
        raise ValueError("Audit requires windows from exactly one provider and match")

    identity = next(iter(identities))
    periods_by_number = {period.period: period for period in periods}

    if len(periods_by_number) != len(periods):
        raise ValueError("Repeated period in clock")

    windows_by_period: dict[int, list[TacticalWindow]] = defaultdict(list)

    for window in windows:
        if window.period not in periods_by_number:
            raise ValueError("Window references an unknown playing period")

        windows_by_period[window.period].append(window)

    issues: list[EventClockIssue] = []

    for event in sorted(events, key=lambda item: item.source_index):
        if (event.provider, event.external_match_id) != identity:
            raise ValueError("Events and windows must share provider and match")

        if event.period == 5:
            continue

        if event.period not in {1, 2, 3, 4} or event.period not in periods_by_number:
            raise ValueError("Event references an unknown playing period")

        candidates = [
            window
            for window in windows_by_period[event.period]
            if window.start_source_index <= event.source_index < window.end_source_index
        ]

        if len(candidates) != 1:
            raise ValueError("Event must belong to exactly one source window")

        window = candidates[0]
        period = periods_by_number[event.period]
        elapsed_ms = period.start_elapsed_ms + event.timestamp_ms

        # A ordem da fonte resolve eventos no mesmo instante dos limites.
        if window.start_elapsed_ms <= elapsed_ms <= window.end_elapsed_ms:
            continue

        issues.append(
            EventClockIssue(
                provider=event.provider,
                external_match_id=event.external_match_id,
                external_event_id=event.external_event_id,
                event_type=event.event_type,
                source_index=event.source_index,
                period=event.period,
                window_number=window.number_in_period,
                reported_elapsed_ms=elapsed_ms,
                window_start_elapsed_ms=window.start_elapsed_ms,
                window_end_elapsed_ms=window.end_elapsed_ms,
                window_start_source_index=window.start_source_index,
                window_end_source_index=window.end_source_index,
            )
        )

    return issues


def require_usable_event_clocks(
    issues: Sequence[EventClockIssue],
    *,
    event_types: set[str],
) -> None:
    affected = [issue for issue in issues if issue.event_type in event_types]

    if affected:
        examples = ", ".join(issue.external_event_id for issue in affected[:5])
        raise ValueError(
            f"Clock issues affect {len(affected)} required events; examples: {examples}"
        )
