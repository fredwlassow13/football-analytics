from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.aerial_statistics import aggregate_player_aerials
from football_analytics.integrations.base import MatchAerialDuel


class TeamAerialStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    external_team_id: str
    won: int = Field(ge=0)
    lost: int = Field(ge=0)
    external_event_ids: tuple[str, ...]

    @property
    def contested(self) -> int:
        return self.won + self.lost

    @property
    def win_pct(self) -> Decimal | None:
        if self.contested == 0:
            return None
        return (Decimal(self.won) * Decimal(100) / Decimal(self.contested)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )


def aggregate_team_aerials(
    aerials: list[MatchAerialDuel],
) -> list[TeamAerialStatistics]:
    players = aggregate_player_aerials(aerials)
    if not players:
        return []

    result: list[TeamAerialStatistics] = []

    for team_id in sorted({player.external_team_id for player in players}):
        team_players = [player for player in players if player.external_team_id == team_id]
        event_ids = tuple(
            event.external_event_id
            for event in sorted(aerials, key=lambda item: item.source_index)
            if event.period in {1, 2, 3, 4} and event.external_team_id == team_id
        )

        summary = TeamAerialStatistics(
            provider=team_players[0].provider,
            external_match_id=team_players[0].external_match_id,
            external_team_id=team_id,
            won=sum(player.won for player in team_players),
            lost=sum(player.lost for player in team_players),
            external_event_ids=event_ids,
        )

        if summary.contested != len(event_ids):
            raise ValueError("Team aerial totals differ from source event count")

        result.append(summary)

    return result
