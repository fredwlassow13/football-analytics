from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ParticipationCalculationResponse(BaseModel):
    id: UUID
    match_id: UUID
    metric_name: str
    formula_version: str
    created_at: datetime


class ParticipationIntervalResponse(BaseModel):
    interval_number: int
    start_ms: int
    end_ms: int
    duration_ms: int


class PlayerParticipationResponse(BaseModel):
    player_id: UUID
    player_name: str
    team_id: UUID
    team_name: str
    is_starter: bool | None
    has_participation_evidence: bool
    elapsed_on_field_ms: int | None
    elapsed_on_field_display: str | None
    intervals: list[ParticipationIntervalResponse]


class MatchParticipationResponse(BaseModel):
    calculation: ParticipationCalculationResponse
    items: list[PlayerParticipationResponse]
    total: int
