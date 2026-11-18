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
    outcome: Literal[
        "complete",
        "incomplete",
        "out",
        "offside",
        "unknown",
        "injury_clearance",
    ]
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


class MatchInterception(MatchEvent):
    """A recorded interception attempt, preserving its explicit result."""

    event_type: Literal["Interception"] = "Interception"
    outcome: Literal[
        "lost", "won", "lost_in_play", "lost_out", "success", "success_in_play", "success_out"
    ]


class MatchBallRecovery(MatchEvent):
    """A loose-ball recovery event; absent flags remain unknown."""

    event_type: Literal["Ball Recovery"] = "Ball Recovery"
    recovery_failure: bool | None = None
    offensive: bool | None = None


class MatchBlock(MatchEvent):
    """A recorded block, including potentially offensive blocks."""

    event_type: Literal["Block"] = "Block"
    deflection: bool | None = None
    offensive: bool | None = None
    save_block: bool | None = None
    counterpress: bool | None = None


class MatchClearance(MatchEvent):
    """A clearance with source-provided body-part and aerial evidence."""

    event_type: Literal["Clearance"] = "Clearance"
    body_part: Literal["head", "left_foot", "right_foot", "other"] | None = None
    aerial_won: bool | None = None
    head: bool | None = None
    left_foot: bool | None = None
    right_foot: bool | None = None
    other: bool | None = None

    @model_validator(mode="after")
    def validate_body_part_evidence(self) -> "MatchClearance":
        flags = {
            "head": self.head,
            "left_foot": self.left_foot,
            "right_foot": self.right_foot,
            "other": self.other,
        }
        marked = [name for name, value in flags.items() if value is True]

        if len(marked) > 1:
            raise ValueError("Conflicting clearance body-part flags")

        if self.body_part is not None:
            if flags[self.body_part] is False:
                raise ValueError("Clearance body part contradicts its flag")
            if marked and marked[0] != self.body_part:
                raise ValueError("Clearance body-part evidence differs")

        return self


class MatchPressure(MatchEvent):
    """A recorded pressure, without an inferred success outcome."""

    event_type: Literal["Pressure"] = "Pressure"
    counterpress: bool | None = None
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    related_event_ids: tuple[str, ...] | None = None


class MatchDribbledPast(MatchEvent):
    """A defender recorded as being beaten by a dribble."""

    event_type: Literal["Dribbled Past"] = "Dribbled Past"
    counterpress: bool | None = None
    related_event_ids: tuple[str, ...] | None = None


class MatchFoulCommitted(MatchEvent):
    """A recorded foul committed, including advantage situations."""

    event_type: Literal["Foul Committed"] = "Foul Committed"
    offensive: bool | None = None
    advantage: bool | None = None
    penalty: bool | None = None
    counterpress: bool | None = None
    foul_type: str | None = Field(default=None, min_length=1)
    card: Literal["yellow_card", "second_yellow", "red_card"] | None = None
    related_event_ids: tuple[str, ...] | None = None


class MatchFoulWon(MatchEvent):
    """A recorded foul won, distinct from its committed counterpart."""

    event_type: Literal["Foul Won"] = "Foul Won"
    defensive: bool | None = None
    advantage: bool | None = None
    penalty: bool | None = None
    related_event_ids: tuple[str, ...] | None = None


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


class MatchGoalkeeperAction(MatchEvent):
    """A goalkeeper action, without inferred shot or save classification."""

    event_type: Literal["Goal Keeper"] = "Goal Keeper"
    goalkeeper_type: Literal[
        "collected",
        "goal_conceded",
        "keeper_sweeper",
        "penalty_conceded",
        "penalty_saved",
        "punch",
        "save",
        "shot_faced",
        "shot_saved",
        "smother",
        "penalty_saved_to_post",
        "saved_to_post",
        "shot_saved_off_target",
        "shot_saved_to_post",
    ]
    outcome: (
        Literal[
            "lost",
            "won",
            "lost_in_play",
            "lost_out",
            "success",
            "success_in_play",
            "success_out",
            "claim",
            "clear",
            "collected_twice",
            "fail",
            "in_play",
            "in_play_danger",
            "in_play_safe",
            "no_touch",
            "saved_twice",
            "touched_in",
            "touched_out",
            "punched_out",
        ]
        | None
    ) = None
    body_part: (
        Literal[
            "both_hands",
            "chest",
            "head",
            "left_foot",
            "left_hand",
            "right_foot",
            "right_hand",
        ]
        | None
    ) = None
    technique: Literal["diving", "standing"] | None = None
    keeper_position: Literal["moving", "prone", "set"] | None = None
    coordinate_system: Literal["statsbomb_120_80"] = "statsbomb_120_80"
    start_location: EventLocation | None = None
    end_location: EventLocation | None = None
    related_event_ids: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def validate_locations(self) -> "MatchGoalkeeperAction":
        for location in (self.start_location, self.end_location):
            if location is not None and location.z is not None:
                raise ValueError("Goalkeeper locations must be two-dimensional")
        return self

    @field_validator("related_event_ids")
    @classmethod
    def validate_related_ids(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if value is not None and any(not event_id for event_id in value):
            raise ValueError("Related event IDs must be nonempty")
        return value


class MatchEventRelations(MatchEvent):
    """Explicit source relationships; targets are not inferred or resolved."""

    related_event_ids: tuple[str, ...] | None = None

    @field_validator("related_event_ids")
    @classmethod
    def validate_related_ids(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if value is not None and any(not event_id for event_id in value):
            raise ValueError("Related event IDs must be nonempty")
        return value


class MatchEventPlayMetadata(MatchEvent):
    """Source event attributes, without inferred continuous ball state."""

    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    out: bool | None = None
    play_pattern: (
        Literal[
            "regular_play",
            "from_kick_off",
            "from_throw_in",
            "from_free_kick",
            "from_keeper",
            "from_corner",
            "from_goal_kick",
            "other",
            "from_counter",
        ]
        | None
    ) = None
    external_play_pattern_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_play_pattern_reference(self) -> "MatchEventPlayMetadata":
        if (self.play_pattern is None) != (self.external_play_pattern_id is None):
            raise ValueError("Play pattern requires both ID and name")
        return self


class MatchPassCreation(MatchPass):
    """A pass with explicit source creation flags and shot reference."""

    shot_assist: bool | None = None
    goal_assist: bool | None = None
    external_assisted_shot_event_id: str | None = Field(default=None, min_length=1)


class MatchShotCreation(MatchShot):
    """A shot with an optional explicit source key-pass reference."""

    external_key_pass_event_id: str | None = Field(default=None, min_length=1)


class MatchBallControlIncident(MatchEvent):
    """Recorded control incident, without inferred possession change."""

    event_type: Literal["Dispossessed", "Miscontrol"]
    under_pressure: bool | None = None
    counterpress: bool | None = None
    out: bool | None = None
    related_event_ids: tuple[str, ...] | None = None

    @field_validator("related_event_ids")
    @classmethod
    def validate_related_ids(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if value is not None and any(not event_id for event_id in value):
            raise ValueError("Related event IDs must be nonempty")
        return value


class MatchEventPressureMetadata(MatchEvent):
    """Recorded pressure flags, without inferred pressure effectiveness."""

    under_pressure: bool | None = None
    counterpress: bool | None = None
