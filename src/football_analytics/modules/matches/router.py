from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from football_analytics.infrastructure.database import get_engine

router = APIRouter(
    prefix="/api/v1/matches",
    tags=["matches"],
)


class MatchResponse(BaseModel):
    id: UUID
    season_id: UUID
    home_team_id: UUID
    home_team_name: str
    away_team_id: UUID
    away_team_name: str
    match_date: date
    kickoff_at: datetime | None
    home_score: int | None
    away_score: int | None


class MatchPage(BaseModel):
    items: list[MatchResponse]
    total: int
    limit: int
    offset: int


@router.get("", response_model=MatchPage)
def list_matches(
    season_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MatchPage:
    filters = ""
    parameters: dict[str, object] = {
        "limit": limit,
        "offset": offset,
    }

    if season_id is not None:
        filters = "WHERE m.season_id = :season_id"
        parameters["season_id"] = season_id

    with get_engine().connect() as connection:
        total: int = connection.execute(
            text(f"""
                SELECT count(*)
                FROM core.matches AS m
                {filters}
            """),
            parameters,
        ).scalar_one()

        rows = connection.execute(
            text(f"""
                SELECT
                    m.id,
                    m.season_id,
                    m.home_team_id,
                    home.name AS home_team_name,
                    m.away_team_id,
                    away.name AS away_team_name,
                    m.match_date,
                    m.kickoff_at,
                    m.home_score,
                    m.away_score
                FROM core.matches AS m
                JOIN core.teams AS home
                    ON home.id = m.home_team_id
                JOIN core.teams AS away
                    ON away.id = m.away_team_id
                {filters}
                ORDER BY m.match_date, m.id
                LIMIT :limit
                OFFSET :offset
            """),
            parameters,
        ).mappings()

        return MatchPage(
            items=[MatchResponse.model_validate(dict(row)) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )
