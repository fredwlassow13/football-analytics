from collections.abc import Sequence
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.integrations.base import (
    MatchDribble,
    MatchEvent,
    MatchEventContext,
    MatchEventSpatial,
    MatchPass,
    MatchShot,
)

PlayerAction = Annotated[
    MatchPass | MatchShot | MatchDribble,
    Field(discriminator="event_type"),
]


class ContextualizedAction(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    action: PlayerAction
    context: MatchEventContext
    spatial: MatchEventSpatial


def _header(event: MatchEvent) -> tuple[object, ...]:
    return (
        event.provider,
        event.external_match_id,
        event.external_event_id,
        event.source_index,
        event.period,
        event.timestamp_ms,
        event.event_type,
        event.external_team_id,
        event.external_player_id,
    )


def _validate_unique_ids(
    events: Sequence[MatchEvent],
    label: str,
) -> None:
    ids = [event.external_event_id for event in events]

    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate event IDs in {label}")


def contextualize_actions(
    *,
    actions: list[MatchPass | MatchShot | MatchDribble],
    contexts: list[MatchEventContext],
    spatial_events: list[MatchEventSpatial],
) -> list[ContextualizedAction]:
    identities = {
        (event.provider, event.external_match_id)
        for event in [*actions, *contexts, *spatial_events]
    }

    if len(identities) > 1:
        raise ValueError("Inputs must belong to one provider and match")

    _validate_unique_ids(actions, "actions")
    _validate_unique_ids(contexts, "contexts")
    _validate_unique_ids(spatial_events, "spatial events")

    contexts_by_id = {event.external_event_id: event for event in contexts}
    spatial_by_id = {event.external_event_id: event for event in spatial_events}

    result: list[ContextualizedAction] = []

    for action in sorted(actions, key=lambda event: event.source_index):
        event_id = action.external_event_id
        context = contexts_by_id.get(event_id)
        spatial = spatial_by_id.get(event_id)

        if context is None:
            raise ValueError(f"Missing context for event {event_id}")

        if spatial is None:
            raise ValueError(f"Missing spatial data for event {event_id}")

        if _header(action) != _header(context) or _header(action) != _header(spatial):
            raise ValueError(f"Inconsistent event header: {event_id}")

        result.append(
            ContextualizedAction(
                action=action,
                context=context,
                spatial=spatial,
            )
        )

    return result
