from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.duel_statistics import (
    TackleOutcomeCounts,
    aggregate_player_duels,
)
from football_analytics.integrations.base import MatchDuel


class TeamDuelStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    external_team_id: str
    tackles: TackleOutcomeCounts
    aerial_lost: int = Field(ge=0)
    external_event_ids: tuple[str, ...]

    @property
    def total_duel_events(self) -> int:
        return self.tackles.total + self.aerial_lost


def aggregate_team_duels(
    duels: list[MatchDuel],
) -> list[TeamDuelStatistics]:
    players = aggregate_player_duels(duels)
    if not players:
        return []

    result: list[TeamDuelStatistics] = []

    for team_id in sorted({player.external_team_id for player in players}):
        team_players = [player for player in players if player.external_team_id == team_id]
        event_ids = tuple(
            event.external_event_id
            for event in sorted(duels, key=lambda item: item.source_index)
            if event.period in {1, 2, 3, 4} and event.external_team_id == team_id
        )

        summary = TeamDuelStatistics(
            provider=team_players[0].provider,
            external_match_id=team_players[0].external_match_id,
            external_team_id=team_id,
            tackles=TackleOutcomeCounts(
                lost=sum(player.tackles.lost for player in team_players),
                won=sum(player.tackles.won for player in team_players),
                lost_in_play=sum(player.tackles.lost_in_play for player in team_players),
                lost_out=sum(player.tackles.lost_out for player in team_players),
                success=sum(player.tackles.success for player in team_players),
                success_in_play=sum(player.tackles.success_in_play for player in team_players),
                success_out=sum(player.tackles.success_out for player in team_players),
            ),
            aerial_lost=sum(player.aerial_lost for player in team_players),
            external_event_ids=event_ids,
        )

        if summary.total_duel_events != len(event_ids):
            raise ValueError("Team duel totals differ from source event count")

        result.append(summary)

    return result
