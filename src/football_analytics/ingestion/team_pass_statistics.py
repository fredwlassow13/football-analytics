from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.pass_statistics import aggregate_player_passes
from football_analytics.integrations.base import MatchPass


class TeamPassStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    external_team_id: str
    completed: int = Field(ge=0)
    incomplete: int = Field(ge=0)
    out: int = Field(ge=0)
    offside: int = Field(ge=0)
    unknown: int = Field(ge=0)
    external_event_ids: tuple[str, ...]

    @property
    def not_completed(self) -> int:
        return self.incomplete + self.out + self.offside

    @property
    def attempted(self) -> int:
        return self.completed + self.not_completed + self.unknown

    @property
    def completion_pct(self) -> Decimal | None:
        if self.attempted == 0:
            return None
        return (Decimal(self.completed) * Decimal(100) / Decimal(self.attempted)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )


def aggregate_team_passes(
    passes: list[MatchPass],
) -> list[TeamPassStatistics]:
    players = aggregate_player_passes(passes)
    if not players:
        return []

    team_ids = sorted({player.external_team_id for player in players})
    result: list[TeamPassStatistics] = []

    for team_id in team_ids:
        team_players = [player for player in players if player.external_team_id == team_id]
        event_ids = tuple(
            event.external_event_id
            for event in sorted(passes, key=lambda item: item.source_index)
            if event.period in {1, 2, 3, 4} and event.external_team_id == team_id
        )

        summary = TeamPassStatistics(
            provider=team_players[0].provider,
            external_match_id=team_players[0].external_match_id,
            external_team_id=team_id,
            completed=sum(player.completed for player in team_players),
            incomplete=sum(player.incomplete for player in team_players),
            out=sum(player.out for player in team_players),
            offside=sum(player.offside for player in team_players),
            unknown=sum(player.unknown for player in team_players),
            external_event_ids=event_ids,
        )

        if summary.attempted != len(event_ids):
            raise ValueError("Team pass totals differ from source event count")

        result.append(summary)

    return result
