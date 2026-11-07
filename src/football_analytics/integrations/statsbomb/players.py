from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import Player, ProviderUnavailable


class CountryReference(BaseModel):
    model_config = ConfigDict(
        strict=True,
        str_strip_whitespace=True,
    )

    name: str = Field(min_length=1)


class StatsBombPlayer(BaseModel):
    model_config = ConfigDict(
        strict=True,
        str_strip_whitespace=True,
    )

    player_id: int = Field(gt=0)
    player_name: str = Field(min_length=1)
    player_nickname: str | None = None
    country: CountryReference | None = None


class TeamLineup(BaseModel):
    model_config = ConfigDict(strict=True)

    team_id: int = Field(gt=0)
    lineup: list[StatsBombPlayer]


def normalize_players(payload: object) -> list[Player]:
    try:
        lineups = TypeAdapter(list[TeamLineup]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid player data") from exc

    players: dict[str, Player] = {}

    for team in lineups:
        for entry in team.lineup:
            player = Player(
                provider="statsbomb",
                external_player_id=str(entry.player_id),
                name=entry.player_name,
                nickname=entry.player_nickname,
                nationality=(entry.country.name if entry.country is not None else None),
            )

            existing = players.get(player.external_player_id)

            if existing is not None and existing != player:
                raise ProviderUnavailable(
                    f"Conflicting data for player {player.external_player_id}"
                )

            players[player.external_player_id] = player

    return sorted(
        players.values(),
        key=lambda player: (
            player.name,
            player.external_player_id,
        ),
    )
