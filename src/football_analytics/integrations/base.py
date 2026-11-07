from datetime import date
from typing import Protocol

from pydantic import BaseModel


class ProviderUnavailable(Exception):
    """The external provider could not return valid data."""


class CompetitionSeason(BaseModel):
    provider: str
    external_competition_id: str
    external_season_id: str
    competition_name: str
    season_name: str
    country_name: str
    gender: str


class CompetitionProvider(Protocol):
    def list_competitions(self) -> list[CompetitionSeason]: ...


class Team(BaseModel):
    provider: str
    external_team_id: str
    name: str


class Match(BaseModel):
    provider: str
    external_match_id: str
    external_competition_id: str
    external_season_id: str
    external_home_team_id: str
    external_away_team_id: str
    match_date: date
    home_score: int | None
    away_score: int | None


class Player(BaseModel):
    provider: str
    external_player_id: str
    name: str
    nickname: str | None = None
    nationality: str | None = None
    birth_date: date | None = None


class MatchLineup(BaseModel):
    provider: str
    external_match_id: str
    external_team_id: str
    external_player_id: str
    shirt_number: int | None = None
    is_starter: bool | None = None
