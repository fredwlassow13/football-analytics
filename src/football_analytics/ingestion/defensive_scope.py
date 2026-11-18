from collections.abc import Sequence

from football_analytics.integrations.base import (
    MatchBallRecovery,
    MatchBlock,
    MatchClearance,
    MatchDribbledPast,
    MatchDuel,
    MatchFoulCommitted,
    MatchFoulWon,
    MatchInterception,
    MatchPressure,
)

type DefensiveEvent = (
    MatchBallRecovery
    | MatchBlock
    | MatchClearance
    | MatchDribbledPast
    | MatchDuel
    | MatchFoulCommitted
    | MatchFoulWon
    | MatchInterception
    | MatchPressure
)

DEFENSIVE_EVENT_MODELS = (
    MatchBallRecovery,
    MatchBlock,
    MatchClearance,
    MatchDribbledPast,
    MatchDuel,
    MatchFoulCommitted,
    MatchFoulWon,
    MatchInterception,
    MatchPressure,
)


def select_defensive_periods(
    events: Sequence[DefensiveEvent],
    *,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None = None,
) -> list[DefensiveEvent]:
    if (
        any(type(period) is not int for period in available_periods)
        or len(available_periods) != len(set(available_periods))
        or set(available_periods) not in ({1, 2}, {1, 2, 3, 4})
    ):
        raise ValueError("Available periods must describe a complete match")

    requested = available_periods if periods is None else periods

    if (
        not requested
        or any(type(period) is not int for period in requested)
        or len(requested) != len(set(requested))
        or not set(requested).issubset(available_periods)
    ):
        raise ValueError("Requested periods are invalid or unavailable")

    identities: set[tuple[str, str]] = set()
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    selected: list[DefensiveEvent] = []

    for event in events:
        if not isinstance(event, DEFENSIVE_EVENT_MODELS):
            raise ValueError("Unsupported defensive event type")

        identities.add((event.provider, event.external_match_id))
        if len(identities) > 1:
            raise ValueError("Events must belong to one provider and match")

        if event.external_event_id in seen_ids or event.source_index in seen_indexes:
            raise ValueError("Duplicate defensive source event")

        seen_ids.add(event.external_event_id)
        seen_indexes.add(event.source_index)

        if event.period not in {*available_periods, 5}:
            raise ValueError("Event period is incompatible with available periods")

        if event.period in requested:
            selected.append(event)

    return sorted(selected, key=lambda item: item.source_index)
