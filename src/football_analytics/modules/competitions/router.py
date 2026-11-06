from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from football_analytics.infrastructure.database import get_engine

router = APIRouter(prefix="/api/v1/competitions", tags=["competitions"])


class Competition(BaseModel):
    id: UUID
    name: str
    country_name: str
    gender: str


class CompetitionPage(BaseModel):
    items: list[Competition]
    total: int
    limit: int
    offset: int


class Season(BaseModel):
    id: UUID
    competition_id: UUID
    name: str


@router.get("", response_model=CompetitionPage)
def list_competitions(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CompetitionPage:
    with get_engine().connect() as conn:
        total: int = conn.execute(text("SELECT count(*) FROM core.competitions")).scalar_one()
        rows = conn.execute(
            text("""SELECT id, name, country_name, gender FROM core.competitions
            ORDER BY name, id LIMIT :limit OFFSET :offset"""),
            {"limit": limit, "offset": offset},
        ).mappings()
        return CompetitionPage(
            items=[Competition.model_validate(dict(r)) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )


@router.get("/{competition_id}/seasons", response_model=list[Season])
def list_seasons(competition_id: UUID) -> list[Season]:
    with get_engine().connect() as conn:
        if (
            conn.execute(
                text("SELECT id FROM core.competitions WHERE id=:id"), {"id": competition_id}
            ).scalar_one_or_none()
            is None
        ):
            raise HTTPException(status_code=404, detail="Competition not found")
        rows = conn.execute(
            text("""SELECT id, competition_id, name FROM core.seasons
            WHERE competition_id=:id ORDER BY name, id"""),
            {"id": competition_id},
        ).mappings()
        return [Season.model_validate(dict(r)) for r in rows]
