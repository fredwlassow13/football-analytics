from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import (
    MatchTacticalSnapshot,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)
from football_analytics.integrations.statsbomb.starting_xi import PlayerReference


class StatsBombTacticalPosition(BaseModel):
    model_config = ConfigDict(strict=True)

    id: int = Field(gt=0)
    name: str = Field(min_length=1)


class StatsBombTacticalPlayer(BaseModel):
    model_config = ConfigDict(strict=True)

    player: PlayerReference
    position: StatsBombTacticalPosition


class StatsBombTactics(BaseModel):
    model_config = ConfigDict(strict=True)

    formation: int = Field(gt=0)
    lineup: list[StatsBombTacticalPlayer] = Field(min_length=1, max_length=11)


class StatsBombEventWithTactics(StatsBombEvent):
    tactics: object = None


def normalize_tactical_snapshots(
    payload: object,
    *,
    match_id: int,
) -> list[MatchTacticalSnapshot]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}

    try:
        source_events = TypeAdapter(list[StatsBombEventWithTactics]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid tactical event data") from exc

    result: list[MatchTacticalSnapshot] = []

    for event in sorted(source_events, key=lambda item: item.index):
        if event.type.name not in {"Starting XI", "Tactical Shift"}:
            continue

        if event.team is None:
            raise ProviderUnavailable(f"Tactical event requires a team: {event.id}")

        try:
            tactics = StatsBombTactics.model_validate(event.tactics)
            header = headers_by_id[event.id]

            snapshot = MatchTacticalSnapshot.model_validate(
                header.model_dump()
                | {
                    "formation": str(tactics.formation),
                    "lineup": [
                        {
                            "external_player_id": str(item.player.id),
                            "external_position_id": str(item.position.id),
                            "position_name": item.position.name,
                        }
                        for item in tactics.lineup
                    ],
                }
            )
        except ValidationError as exc:
            raise ProviderUnavailable(
                f"Invalid tactical details for StatsBomb event {event.id}"
            ) from exc

        result.append(snapshot)

    return result
