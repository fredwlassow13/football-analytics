from collections import Counter, defaultdict

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.integrations.base import MatchShot


class PlayerShotStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    provider: str
    external_match_id: str
    external_team_id: str
    external_player_id: str
    goals: int = Field(ge=0)
    saved: int = Field(ge=0)
    saved_to_post: int = Field(ge=0)
    saved_off_target: int = Field(ge=0)
    blocked: int = Field(ge=0)
    off_target: int = Field(ge=0)
    post: int = Field(ge=0)
    wayward: int = Field(ge=0)

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


def aggregate_player_shots(
    shots: list[MatchShot],
) -> list[PlayerShotStatistics]:
    identities = {(event.provider, event.external_match_id) for event in shots}

    if len(identities) > 1:
        raise ValueError("Shots must belong to one provider and match")

    if not shots:
        return []

    provider, match_id = next(iter(identities))
    seen_ids: set[str] = set()
    player_teams: dict[str, str] = {}
    grouped: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    for event in shots:
        if event.external_event_id in seen_ids:
            raise ValueError(f"Duplicate shot event: {event.external_event_id}")

        seen_ids.add(event.external_event_id)

        if event.period not in {1, 2, 3, 4, 5}:
            raise ValueError(f"Unsupported shot period: {event.period}")

        if event.period == 5:
            continue

        team_id = event.external_team_id
        player_id = event.external_player_id

        if team_id is None or player_id is None:
            raise ValueError("Shot statistics require team and player references")

        previous_team = player_teams.get(player_id)

        if previous_team is not None and previous_team != team_id:
            raise ValueError(f"Player belongs to multiple teams: {player_id}")

        player_teams[player_id] = team_id
        grouped[(team_id, player_id)][event.outcome] += 1

    return [
        PlayerShotStatistics(
            provider=provider,
            external_match_id=match_id,
            external_team_id=team_id,
            external_player_id=player_id,
            goals=counts["goal"],
            saved=counts["saved"],
            saved_to_post=counts["saved_to_post"],
            saved_off_target=counts["saved_off_target"],
            blocked=counts["blocked"],
            off_target=counts["off_target"],
            post=counts["post"],
            wayward=counts["wayward"],
        )
        for (team_id, player_id), counts in sorted(grouped.items())
    ]
