from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import MatchLineup, ProviderUnavailable


class LineupPlayer(BaseModel):
    model_config = ConfigDict(strict=True)

    player_id: int = Field(gt=0)
    jersey_number: int | None = Field(default=None, ge=0)


class TeamLineup(BaseModel):
    model_config = ConfigDict(strict=True)

    team_id: int = Field(gt=0)
    lineup: list[LineupPlayer]


def normalize_lineups(
    payload: object,
    match_id: int,
    starting_xi: dict[str, set[str]] | None = None,
) -> list[MatchLineup]:
    if match_id <= 0:
        raise ValueError("Match ID must be positive")

    try:
        teams = TypeAdapter(list[TeamLineup]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid lineup data") from exc

    lineups: dict[str, MatchLineup] = {}

    for team in teams:
        for player in team.lineup:
            item = MatchLineup(
                provider="statsbomb",
                external_match_id=str(match_id),
                external_team_id=str(team.team_id),
                external_player_id=str(player.player_id),
                shirt_number=player.jersey_number,
            )

            existing = lineups.get(item.external_player_id)

            if existing is not None and existing != item:
                raise ProviderUnavailable(
                    f"Conflicting lineup data for player {item.external_player_id}"
                )

            lineups[item.external_player_id] = item

    if starting_xi is not None:
        listed_by_team: dict[str, set[str]] = {str(team.team_id): set() for team in teams}

        for item in lineups.values():
            listed_by_team[item.external_team_id].add(item.external_player_id)

        if len(starting_xi) != 2:
            raise ProviderUnavailable("Expected starting lineups for exactly two teams")

        if set(starting_xi) != set(listed_by_team):
            raise ProviderUnavailable("Starting XI teams differ from lineup teams")

        for team_id, starters in starting_xi.items():
            if len(starters) != 11:
                raise ProviderUnavailable(f"Expected 11 distinct starters for team {team_id}")

            if not starters.issubset(listed_by_team[team_id]):
                raise ProviderUnavailable(
                    f"Starting XI contains unlisted players for team {team_id}"
                )

        for item in lineups.values():
            item.is_starter = item.external_player_id in starting_xi[item.external_team_id]
    return sorted(
        lineups.values(),
        key=lambda item: (
            item.external_team_id,
            item.external_player_id,
        ),
    )
