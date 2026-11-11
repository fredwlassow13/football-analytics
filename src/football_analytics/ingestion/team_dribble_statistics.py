from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.dribble_statistics import aggregate_player_dribbles
from football_analytics.integrations.base import MatchDribble


class TeamDribbleStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    external_team_id: str
    completed: int = Field(ge=0)
    incomplete: int = Field(ge=0)
    external_event_ids: tuple[str, ...]

    @property
    def attempted(self) -> int:
        return self.completed + self.incomplete

    @property
    def completion_pct(self) -> Decimal | None:
        if self.attempted == 0:
            return None
        return (Decimal(self.completed) * Decimal(100) / Decimal(self.attempted)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )


def aggregate_team_dribbles(
    dribbles: list[MatchDribble],
) -> list[TeamDribbleStatistics]:
    players = aggregate_player_dribbles(dribbles)
    if not players:
        return []

    result: list[TeamDribbleStatistics] = []

    for team_id in sorted({player.external_team_id for player in players}):
        team_players = [player for player in players if player.external_team_id == team_id]
        event_ids = tuple(
            event.external_event_id
            for event in sorted(dribbles, key=lambda item: item.source_index)
            if event.period in {1, 2, 3, 4} and event.external_team_id == team_id
        )

        summary = TeamDribbleStatistics(
            provider=team_players[0].provider,
            external_match_id=team_players[0].external_match_id,
            external_team_id=team_id,
            completed=sum(player.completed for player in team_players),
            incomplete=sum(player.incomplete for player in team_players),
            external_event_ids=event_ids,
        )

        if summary.attempted != len(event_ids):
            raise ValueError("Team dribble totals differ from source event count")

        result.append(summary)

    return result
