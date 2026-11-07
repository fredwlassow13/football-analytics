from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import ProviderUnavailable


class EventType(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str


class TeamReference(BaseModel):
    model_config = ConfigDict(strict=True)

    id: int = Field(gt=0)


class EventHeader(BaseModel):
    model_config = ConfigDict(strict=True)

    type: EventType
    team: TeamReference | None = None
    tactics: object = None


class PlayerReference(BaseModel):
    model_config = ConfigDict(strict=True)

    id: int = Field(gt=0)


class StartingPlayer(BaseModel):
    model_config = ConfigDict(strict=True)

    player: PlayerReference


class StartingTactics(BaseModel):
    model_config = ConfigDict(strict=True)

    lineup: list[StartingPlayer] = Field(
        min_length=11,
        max_length=11,
    )


def normalize_starting_xi(
    payload: object,
) -> dict[str, set[str]]:
    try:
        events = TypeAdapter(list[EventHeader]).validate_python(payload)

        result: dict[str, set[str]] = {}

        for event in events:
            if event.type.name != "Starting XI":
                continue

            if event.team is None:
                raise ProviderUnavailable("Starting XI event has no team")

            team_id = str(event.team.id)

            if team_id in result:
                raise ProviderUnavailable(f"Repeated Starting XI event for team {team_id}")

            tactics = StartingTactics.model_validate(event.tactics)

            player_ids = {str(item.player.id) for item in tactics.lineup}

            if len(player_ids) != 11:
                raise ProviderUnavailable(f"Expected 11 distinct starters for team {team_id}")

            result[team_id] = player_ids

    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid Starting XI data") from exc

    if len(result) != 2:
        raise ProviderUnavailable("Expected Starting XI events for exactly two teams")

    return result
