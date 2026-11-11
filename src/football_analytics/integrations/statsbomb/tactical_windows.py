from football_analytics.ingestion.tactical_window_catalogue import (
    TacticalBoundary,
    TacticalWindow,
    build_tactical_window_catalogue,
)
from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.events import normalize_events
from football_analytics.integrations.statsbomb.participation_events import (
    normalize_participation_events,
)
from football_analytics.integrations.statsbomb.period_clock import (
    normalize_period_clock,
)


def normalize_tactical_boundaries(
    payload: object,
    *,
    match_id: int,
) -> list[TacticalBoundary]:
    events = normalize_events(payload, match_id=match_id)
    periods = normalize_period_clock(payload)
    participation_actions = normalize_participation_events(payload)

    dismissal_indexes = {
        action.event_index
        for action in participation_actions
        if action.reason in {"Red Card", "Second Yellow"}
    }

    result: list[TacticalBoundary] = []

    for period in periods:
        period_events = sorted(
            (event for event in events if event.period == period.period),
            key=lambda event: event.source_index,
        )

        if not period_events:
            raise ProviderUnavailable("Playing period has no source events")

        result.extend(
            [
                TacticalBoundary(
                    period=period.period,
                    elapsed_ms=period.start_elapsed_ms,
                    source_index=period_events[0].source_index,
                    reason="period_start",
                ),
                TacticalBoundary(
                    period=period.period,
                    elapsed_ms=period.end_elapsed_ms,
                    source_index=period_events[-1].source_index + 1,
                    reason="period_end",
                ),
            ]
        )

        for event in period_events:
            if event.timestamp_ms > period.duration_ms:
                raise ProviderUnavailable(
                    f"Event exceeds period duration: {event.external_event_id}"
                )

            elapsed_ms = period.start_elapsed_ms + event.timestamp_ms

            if event.event_type == "Substitution":
                result.append(
                    TacticalBoundary(
                        period=event.period,
                        elapsed_ms=elapsed_ms,
                        source_index=event.source_index,
                        reason="substitution",
                        source_event_id=event.external_event_id,
                    )
                )

            if event.event_type == "Tactical Shift":
                result.append(
                    TacticalBoundary(
                        period=event.period,
                        elapsed_ms=elapsed_ms,
                        source_index=event.source_index,
                        reason="tactical_shift",
                        source_event_id=event.external_event_id,
                    )
                )

            if event.source_index in dismissal_indexes:
                result.append(
                    TacticalBoundary(
                        period=event.period,
                        elapsed_ms=elapsed_ms,
                        source_index=event.source_index,
                        reason="dismissal",
                        source_event_id=event.external_event_id,
                    )
                )

    return sorted(
        result,
        key=lambda boundary: (
            boundary.period,
            boundary.source_index,
            boundary.reason,
        ),
    )


def normalize_tactical_windows(
    payload: object,
    *,
    match_id: int,
) -> list[TacticalWindow]:
    boundaries = normalize_tactical_boundaries(payload, match_id=match_id)

    try:
        return build_tactical_window_catalogue(
            boundaries,
            provider="statsbomb",
            external_match_id=str(match_id),
        )
    except ValueError as exc:
        raise ProviderUnavailable(
            "StatsBomb returned an inconsistent tactical window catalogue"
        ) from exc
