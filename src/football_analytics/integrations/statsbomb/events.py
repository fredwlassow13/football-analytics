from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import MatchEvent, ProviderUnavailable
from football_analytics.integrations.statsbomb.clock import timestamp_to_milliseconds
from football_analytics.integrations.statsbomb.starting_xi import (
    PlayerReference,
    TeamReference,
)


class StatsBombEventType(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str = Field(min_length=1)


class StatsBombEvent(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str = Field(min_length=1)
    index: int = Field(gt=0)
    period: int = Field(ge=1, le=5)
    timestamp: str
    type: StatsBombEventType
    team: TeamReference | None = None
    player: PlayerReference | None = None


def normalize_events(
    payload: object,
    *,
    match_id: int,
) -> list[MatchEvent]:
    if isinstance(match_id, bool) or match_id <= 0:
        raise ValueError("Match ID must be positive")

    try:
        events = TypeAdapter(list[StatsBombEvent]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid event data") from exc

    event_ids: set[str] = set()
    event_indexes: set[int] = set()
    result: list[MatchEvent] = []

    for event in sorted(events, key=lambda item: item.index):
        if event.id in event_ids:
            raise ProviderUnavailable(f"Repeated StatsBomb event ID: {event.id}")

        if event.index in event_indexes:
            raise ProviderUnavailable(f"Repeated StatsBomb event index: {event.index}")

        event_ids.add(event.id)
        event_indexes.add(event.index)

        try:
            timestamp_ms = timestamp_to_milliseconds(event.timestamp)
        except ValueError as exc:
            raise ProviderUnavailable(f"Invalid timestamp for StatsBomb event {event.id}") from exc

        result.append(
            MatchEvent(
                provider="statsbomb",
                external_match_id=str(match_id),
                external_event_id=event.id,
                source_index=event.index,
                period=event.period,
                timestamp_ms=timestamp_ms,
                event_type=event.type.name,
                external_team_id=str(event.team.id) if event.team is not None else None,
                external_player_id=str(event.player.id) if event.player is not None else None,
            )
        )

    return result
