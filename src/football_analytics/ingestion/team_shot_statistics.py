from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.shot_statistics import aggregate_player_shots
from football_analytics.integrations.base import MatchShot


class TeamShotStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    external_team_id: str
    goals: int = Field(ge=0)
    saved: int = Field(ge=0)
    saved_to_post: int = Field(ge=0)
    saved_off_target: int = Field(ge=0)
    blocked: int = Field(ge=0)
    off_target: int = Field(ge=0)
    post: int = Field(ge=0)
    wayward: int = Field(ge=0)
    external_event_ids: tuple[str, ...]

    @property
    def shots_on_target(self) -> int:
        return self.goals + self.saved + self.saved_to_post

    @property
    def shots(self) -> int:
        return (
            self.shots_on_target
            + self.saved_off_target
            + self.blocked
            + self.off_target
            + self.post
            + self.wayward
        )


def aggregate_team_shots(
    shots: list[MatchShot],
) -> list[TeamShotStatistics]:
    players = aggregate_player_shots(shots)
    if not players:
        return []

    result: list[TeamShotStatistics] = []

    for team_id in sorted({player.external_team_id for player in players}):
        team_players = [player for player in players if player.external_team_id == team_id]
        event_ids = tuple(
            event.external_event_id
            for event in sorted(shots, key=lambda item: item.source_index)
            if event.period in {1, 2, 3, 4} and event.external_team_id == team_id
        )

        summary = TeamShotStatistics(
            provider=team_players[0].provider,
            external_match_id=team_players[0].external_match_id,
            external_team_id=team_id,
            goals=sum(player.goals for player in team_players),
            saved=sum(player.saved for player in team_players),
            saved_to_post=sum(player.saved_to_post for player in team_players),
            saved_off_target=sum(player.saved_off_target for player in team_players),
            blocked=sum(player.blocked for player in team_players),
            off_target=sum(player.off_target for player in team_players),
            post=sum(player.post for player in team_players),
            wayward=sum(player.wayward for player in team_players),
            external_event_ids=event_ids,
        )

        if summary.shots != len(event_ids):
            raise ValueError("Team shot totals differ from source event count")

        result.append(summary)

    return result
