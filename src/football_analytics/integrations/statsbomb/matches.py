from datetime import date

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import Match, ProviderUnavailable


class CompetitionReference(BaseModel):
    model_config = ConfigDict(strict=True)

    competition_id: int = Field(gt=0)


class SeasonReference(BaseModel):
    model_config = ConfigDict(strict=True)

    season_id: int = Field(gt=0)


class HomeTeamReference(BaseModel):
    model_config = ConfigDict(strict=True)

    home_team_id: int = Field(gt=0)


class AwayTeamReference(BaseModel):
    model_config = ConfigDict(strict=True)

    away_team_id: int = Field(gt=0)


class StatsBombMatch(BaseModel):
    model_config = ConfigDict(strict=True)

    match_id: int = Field(gt=0)
    match_date: str
    competition: CompetitionReference
    season: SeasonReference
    home_team: HomeTeamReference
    away_team: AwayTeamReference
    home_score: int | None = Field(default=None, ge=0)
    away_score: int | None = Field(default=None, ge=0)


def normalize_matches(payload: object) -> list[Match]:
    try:
        entries = TypeAdapter(list[StatsBombMatch]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid match data") from exc

    matches: dict[str, Match] = {}

    for entry in entries:
        if entry.home_team.home_team_id == entry.away_team.away_team_id:
            raise ProviderUnavailable(f"Same home and away team in match {entry.match_id}")

        try:
            match_date = date.fromisoformat(entry.match_date)
        except ValueError as exc:
            raise ProviderUnavailable(f"Invalid date in match {entry.match_id}") from exc

        match = Match(
            provider="statsbomb",
            external_match_id=str(entry.match_id),
            external_competition_id=str(entry.competition.competition_id),
            external_season_id=str(entry.season.season_id),
            external_home_team_id=str(entry.home_team.home_team_id),
            external_away_team_id=str(entry.away_team.away_team_id),
            match_date=match_date,
            home_score=entry.home_score,
            away_score=entry.away_score,
        )

        existing = matches.get(match.external_match_id)
        if existing is not None and existing != match:
            raise ProviderUnavailable(f"Conflicting data for match {match.external_match_id}")

        matches[match.external_match_id] = match

    return sorted(
        matches.values(),
        key=lambda match: (match.match_date, match.external_match_id),
    )
