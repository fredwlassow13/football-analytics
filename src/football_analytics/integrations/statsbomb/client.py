import logging
import math
import time

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import CompetitionSeason, ProviderUnavailable

logger = logging.getLogger(__name__)


class StatsBombCompetition(BaseModel):
    model_config = ConfigDict(strict=True)

    competition_id: int = Field(gt=0)
    season_id: int = Field(gt=0)
    competition_name: str
    season_name: str
    country_name: str
    competition_gender: str


class StatsBombClient:
    """Adapter for the public JSON dataset; the caller owns the HTTP client."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def _fetch(self, path: str) -> httpx.Response:
        for attempt in range(3):
            try:
                response = self.client.get(path)
                response.raise_for_status()
                return response

            except (ValueError, ValidationError) as exc:
                raise ProviderUnavailable("StatsBomb returned invalid data") from exc
            except httpx.HTTPError as exc:
                transient = isinstance(exc, httpx.TransportError) or (
                    isinstance(exc, httpx.HTTPStatusError)
                    and (exc.response.status_code == 429 or exc.response.status_code >= 500)
                )
                if not transient or attempt == 2:
                    logger.warning("StatsBomb request failed", exc_info=True)
                    raise ProviderUnavailable("StatsBomb is unavailable") from exc
                # Do not retry before the provider's requested delay. Long delays
                # are returned to the caller as unavailability instead of blocking workers.
                delay = float(2**attempt)
                if isinstance(exc, httpx.HTTPStatusError):
                    retry_after = exc.response.headers.get("Retry-After")
                    if retry_after is not None:
                        try:
                            requested = float(retry_after)
                        except ValueError:
                            raise ProviderUnavailable("StatsBomb requested a later retry") from exc
                        if not math.isfinite(requested) or requested > 5 or requested < 0:
                            raise ProviderUnavailable("StatsBomb requested a later retry") from exc
                        delay = max(delay, requested)
                time.sleep(delay)
        raise ProviderUnavailable("StatsBomb is unavailable")  # pragma: no cover

    @staticmethod
    def normalize_catalog(payload: object) -> list[CompetitionSeason]:
        try:
            entries = TypeAdapter(list[StatsBombCompetition]).validate_python(payload)
        except ValidationError as exc:
            raise ProviderUnavailable("StatsBomb returned invalid data") from exc
        return [
            CompetitionSeason(
                provider="statsbomb",
                external_competition_id=str(entry.competition_id),
                external_season_id=str(entry.season_id),
                competition_name=entry.competition_name,
                season_name=entry.season_name,
                country_name=entry.country_name,
                gender=entry.competition_gender,
            )
            for entry in entries
        ]

    def list_competitions(self) -> list[CompetitionSeason]:
        try:
            return self.normalize_catalog(self.fetch_catalog().json())
        except ValueError as exc:
            raise ProviderUnavailable("StatsBomb returned invalid JSON") from exc

    def fetch_catalog(self) -> httpx.Response:
        return self._fetch("competitions.json")

    def fetch_matches(
        self,
        competition_id: int,
        season_id: int,
    ) -> httpx.Response:
        if competition_id <= 0 or season_id <= 0:
            raise ValueError("Competition and season IDs must be positive")

        return self._fetch(f"matches/{competition_id}/{season_id}.json")

    def fetch_lineups(self, match_id: int) -> httpx.Response:
        if match_id <= 0:
            raise ValueError("Match ID must be positive")

        return self._fetch(f"lineups/{match_id}.json")

    def fetch_events(self, match_id: int) -> httpx.Response:
        if match_id <= 0:
            raise ValueError("Match ID must be positive")

        return self._fetch(f"events/{match_id}.json")
