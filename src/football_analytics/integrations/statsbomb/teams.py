from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import ProviderUnavailable, Team


class HomeTeam(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    home_team_id: int = Field(gt=0)
    home_team_name: str = Field(min_length=1)


class AwayTeam(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    away_team_id: int = Field(gt=0)
    away_team_name: str = Field(min_length=1)


class MatchTeams(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    home_team: HomeTeam
    away_team: AwayTeam


def normalize_teams(payload: object) -> list[Team]:
    try:
        matches = TypeAdapter(list[MatchTeams]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid team data") from exc

    teams: dict[str, Team] = {}

    for match in matches:
        if match.home_team.home_team_id == match.away_team.away_team_id:
            raise ProviderUnavailable("Same home and away team in match")
        candidates = [
            (
                match.home_team.home_team_id,
                match.home_team.home_team_name,
            ),
            (
                match.away_team.away_team_id,
                match.away_team.away_team_name,
            ),
        ]

        for external_id, name in candidates:
            key = str(external_id)

            if key in teams and teams[key].name != name:
                raise ProviderUnavailable(f"Conflicting names for StatsBomb team {key}")

            teams[key] = Team(
                provider="statsbomb",
                external_team_id=key,
                name=name,
            )

    return sorted(teams.values(), key=lambda team: team.name)
