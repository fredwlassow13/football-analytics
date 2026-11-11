from collections import Counter, defaultdict

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.integrations.base import MatchDuel


class TackleOutcomeCounts(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    lost: int = Field(default=0, ge=0)
    won: int = Field(default=0, ge=0)
    lost_in_play: int = Field(default=0, ge=0)
    lost_out: int = Field(default=0, ge=0)
    success: int = Field(default=0, ge=0)
    success_in_play: int = Field(default=0, ge=0)
    success_out: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return (
            self.lost
            + self.won
            + self.lost_in_play
            + self.lost_out
            + self.success
            + self.success_in_play
            + self.success_out
        )


class PlayerDuelStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    external_team_id: str
    external_player_id: str
    tackles: TackleOutcomeCounts
    aerial_lost: int = Field(ge=0)
    external_event_ids: tuple[str, ...]

    @property
    def total_duel_events(self) -> int:
        return self.tackles.total + self.aerial_lost


def aggregate_player_duels(
    duels: list[MatchDuel],
) -> list[PlayerDuelStatistics]:
    identities = {(event.provider, event.external_match_id) for event in duels}
    if len(identities) > 1:
        raise ValueError("Duels must belong to one provider and match")
    if not duels:
        return []

    provider, match_id = next(iter(identities))
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    player_teams: dict[str, str] = {}
    grouped: dict[tuple[str, str], list[MatchDuel]] = defaultdict(list)

    for event in sorted(duels, key=lambda item: item.source_index):
        if event.external_event_id in seen_ids or event.source_index in seen_indexes:
            raise ValueError("Duplicate duel source event")
        seen_ids.add(event.external_event_id)
        seen_indexes.add(event.source_index)

        if event.period not in {1, 2, 3, 4, 5}:
            raise ValueError("Unsupported duel period")
        if event.period == 5:
            continue

        team_id = event.external_team_id
        player_id = event.external_player_id
        if team_id is None or player_id is None:
            raise ValueError("Duel statistics require team and player references")

        if player_id in player_teams and player_teams[player_id] != team_id:
            raise ValueError("Player belongs to multiple teams")
        player_teams[player_id] = team_id

        if event.duel_type == "aerial_lost" and event.outcome not in {None, "lost"}:
            raise ValueError("Aerial lost outcome requires review")

        grouped[(team_id, player_id)].append(event)

    result: list[PlayerDuelStatistics] = []

    for (team_id, player_id), events in sorted(grouped.items()):
        counts = Counter(event.outcome for event in events if event.duel_type == "tackle")
        tackles = TackleOutcomeCounts(
            lost=counts["lost"],
            won=counts["won"],
            lost_in_play=counts["lost_in_play"],
            lost_out=counts["lost_out"],
            success=counts["success"],
            success_in_play=counts["success_in_play"],
            success_out=counts["success_out"],
        )
        summary = PlayerDuelStatistics(
            provider=provider,
            external_match_id=match_id,
            external_team_id=team_id,
            external_player_id=player_id,
            tackles=tackles,
            aerial_lost=sum(event.duel_type == "aerial_lost" for event in events),
            external_event_ids=tuple(event.external_event_id for event in events),
        )

        if summary.total_duel_events != len(events):
            raise ValueError("Duel totals differ from source event count")

        result.append(summary)

    return result
