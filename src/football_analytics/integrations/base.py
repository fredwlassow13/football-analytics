from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class MatchEvent(BaseModel):
    """Common event data; timestamp_ms is relative to its period."""

    model_config = ConfigDict(strict=True, extra="forbid")

    provider: str = Field(min_length=1)
    external_match_id: str = Field(min_length=1)
    external_event_id: str = Field(min_length=1)
    source_index: int = Field(gt=0)
    period: int = Field(ge=1)
    timestamp_ms: int = Field(ge=0)
    event_type: str = Field(min_length=1)
    external_team_id: str | None = Field(default=None, min_length=1)
    external_player_id: str | None = Field(default=None, min_length=1)


class MatchPass(MatchEvent):
    """A pass with outcome and optional source-provided attributes."""

    event_type: Literal["Pass"] = "Pass"
    outcome: Literal["complete", "incomplete", "out", "offside", "unknown"]
    external_recipient_player_id: str | None = Field(default=None, min_length=1)
    pass_type: str | None = Field(default=None, min_length=1)

    @property
    def is_completed(self) -> bool | None:
        if self.outcome == "unknown":
            return None

        return self.outcome == "complete"


class MatchShot(MatchEvent):
    """A shot with outcome and optional source-provided attributes."""

    event_type: Literal["Shot"] = "Shot"
    outcome: Literal[
        "goal",
        "saved",
        "saved_to_post",
        "saved_off_target",
        "blocked",
        "off_target",
        "post",
        "wayward",
    ]
    body_part: Literal["left_foot", "right_foot", "head", "other"] | None = None
    technique: str | None = Field(default=None, min_length=1)
    shot_type: str | None = Field(default=None, min_length=1)
    statsbomb_xg: float | None = Field(
        default=None,
        ge=0,
        le=1,
        allow_inf_nan=False,
    )

    @property
    def is_goal(self) -> bool:
        return self.outcome == "goal"

    @property
    def is_on_target(self) -> bool:
        return self.outcome in {"goal", "saved", "saved_to_post"}


class MatchDribble(MatchEvent):
    """A take-on attempt, distinct from a carry."""

    event_type: Literal["Dribble"] = "Dribble"
    outcome: Literal["complete", "incomplete"]

    @property
    def is_completed(self) -> bool:
        return self.outcome == "complete"


class MatchDuel(MatchEvent):
    """A recorded duel, preserving subtype and source outcome."""

    event_type: Literal["Duel"] = "Duel"
    duel_type: Literal["tackle", "aerial_lost"]
    outcome: (
        Literal[
            "lost",
            "won",
            "lost_in_play",
            "lost_out",
            "success",
            "success_in_play",
            "success_out",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def require_tackle_outcome(self) -> "MatchDuel":
        if self.duel_type == "tackle" and self.outcome is None:
            raise ValueError("Tackle requires an explicit outcome")
        return self


class MatchAerialDuel(MatchEvent):
    """An aerial result derived from explicit source evidence."""

    event_type: Literal["Pass", "Clearance", "Shot", "Miscontrol", "Duel"]
    outcome: Literal["won", "lost"]
    evidence: Literal["aerial_won_flag", "aerial_lost_subtype"]

    @model_validator(mode="after")
    def validate_evidence(self) -> "MatchAerialDuel":
        if self.outcome == "won":
            if self.event_type == "Duel" or self.evidence != "aerial_won_flag":
                raise ValueError("Aerial win requires an explicit aerial_won flag")
        elif self.event_type != "Duel" or self.evidence != "aerial_lost_subtype":
            raise ValueError("Aerial loss requires an Aerial Lost duel")

        return self


class TacticalPlayer(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    external_player_id: str = Field(min_length=1)
    external_position_id: str = Field(min_length=1)
    position_name: str = Field(min_length=1)


class MatchTacticalSnapshot(MatchEvent):
    """A recorded tactical lineup, not continuous player tracking."""

    event_type: Literal["Starting XI", "Tactical Shift"]
    formation: str = Field(min_length=1)
    lineup: list[TacticalPlayer] = Field(min_length=1, max_length=11)

    @field_validator("lineup")
    @classmethod
    def validate_distinct_players(
        cls,
        lineup: list[TacticalPlayer],
    ) -> list[TacticalPlayer]:
        player_ids = {player.external_player_id for player in lineup}

        if len(player_ids) != len(lineup):
            raise ValueError("Tactical lineup contains duplicate players")

        return lineup


class EventPosition(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    external_position_id: str = Field(min_length=1)
    position_name: str = Field(min_length=1)


class MatchEventContext(MatchEvent):
    """Context recorded for an event, not continuous player positioning."""

    position: EventPosition | None = None
    possession_sequence: int | None = Field(default=None, gt=0)
    external_possession_team_id: str | None = Field(default=None, min_length=1)
    under_pressure: bool | None = None

    @property
    def event_team_has_possession(self) -> bool | None:
        if self.external_team_id is None or self.external_possession_team_id is None:
            return None

        return self.external_team_id == self.external_possession_team_id


class EventLocation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    z: float | None = Field(default=None, allow_inf_nan=False)


class MatchEventSpatial(MatchEvent):
    """Event coordinates preserved in the declared source coordinate system."""

    coordinate_system: Literal["statsbomb_120_80"]
    start_location: EventLocation | None = None
    end_location: EventLocation | None = None
