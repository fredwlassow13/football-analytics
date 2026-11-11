from collections.abc import Sequence

from football_analytics.integrations.base import (
    MatchDribble,
    MatchEvent,
    MatchPass,
    MatchShot,
)


def select_offensive_periods[T: MatchEvent](
    events: Sequence[T],
    *,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None = None,
) -> list[T]:
    if (
        any(isinstance(period, bool) or not isinstance(period, int) for period in available_periods)
        or len(available_periods) != len(set(available_periods))
        or set(available_periods) not in ({1, 2}, {1, 2, 3, 4})
    ):
        raise ValueError("Available periods must describe a complete match")

    selected_periods = available_periods if periods is None else periods

    if (
        not selected_periods
        or any(
            isinstance(period, bool) or not isinstance(period, int) for period in selected_periods
        )
        or len(selected_periods) != len(set(selected_periods))
        or not set(selected_periods).issubset(available_periods)
    ):
        raise ValueError("Requested periods are invalid or unavailable")

    identities: set[tuple[str, str]] = set()
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    result: list[T] = []

    for event in sorted(events, key=lambda item: item.source_index):
        if not isinstance(event, MatchPass | MatchShot | MatchDribble):
            raise ValueError("Unsupported offensive event type")

        identities.add((event.provider, event.external_match_id))
        if len(identities) > 1:
            raise ValueError("Events must belong to one provider and match")

        if event.external_event_id in seen_ids or event.source_index in seen_indexes:
            raise ValueError("Duplicate offensive source event")

        seen_ids.add(event.external_event_id)
        seen_indexes.add(event.source_index)

        if event.period not in {*available_periods, 5}:
            raise ValueError("Event period is incompatible with available periods")

        if event.period in selected_periods:
            result.append(event)

    return result
