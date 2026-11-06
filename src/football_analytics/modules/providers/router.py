from collections.abc import Iterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from football_analytics.infrastructure.settings import get_settings
from football_analytics.integrations.base import (
    CompetitionProvider,
    CompetitionSeason,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.client import StatsBombClient

router = APIRouter(prefix="/api/v1/providers/statsbomb", tags=["providers"])


class CompetitionPage(BaseModel):
    items: list[CompetitionSeason]
    total: int
    offset: int
    limit: int


def get_provider() -> Iterator[CompetitionProvider]:
    with httpx.Client(
        base_url=get_settings().statsbomb_base_url,
        timeout=httpx.Timeout(15, connect=5),
        follow_redirects=True,
        headers={"User-Agent": "football-analytics/0.1.0"},
    ) as client:
        yield StatsBombClient(client)


@router.get("/competitions", response_model=CompetitionPage)
def list_competitions(
    provider: Annotated[CompetitionProvider, Depends(get_provider)],
    country: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CompetitionPage:
    """Preview available competition/season pairs; does not persist data.

    IDs are external StatsBomb IDs, not the platform's internal identifiers.
    Data source: StatsBomb Open Data (https://github.com/statsbomb/open-data).
    """
    try:
        items = provider.list_competitions()
    except ProviderUnavailable as exc:
        raise HTTPException(
            status_code=502, detail="StatsBomb unavailable; try again later"
        ) from exc
    if country:
        items = [item for item in items if item.country_name.casefold() == country.casefold()]
    items.sort(
        key=lambda item: (
            item.competition_name,
            item.season_name,
            item.external_competition_id,
            item.external_season_id,
        )
    )
    return CompetitionPage(
        items=items[offset : offset + limit], total=len(items), offset=offset, limit=limit
    )
