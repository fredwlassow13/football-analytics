from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.integrations.base import MatchAerialDuel


class PlayerAerialStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    external_team_id: str
    external_player_id: str
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


def aggregate_player_aerials(
    aerials: list[MatchAerialDuel],
) -> list[PlayerAerialStatistics]:
    identities = {(event.provider, event.external_match_id) for event in aerials}
    if len(identities) > 1:
        raise ValueError("Aerials must belong to one provider and match")
    if not aerials:
        return []

    provider, match_id = next(iter(identities))
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    player_teams: dict[str, str] = {}
    grouped: dict[tuple[str, str], list[MatchAerialDuel]] = defaultdict(list)

    for event in sorted(aerials, key=lambda item: item.source_index):
        if event.external_event_id in seen_ids or event.source_index in seen_indexes:
            raise ValueError("Duplicate aerial source event")
        seen_ids.add(event.external_event_id)
        seen_indexes.add(event.source_index)

        if event.period not in {1, 2, 3, 4, 5}:
            raise ValueError("Unsupported aerial period")
        if event.period == 5:
            continue

        team_id = event.external_team_id
        player_id = event.external_player_id

        if team_id is None or player_id is None:
            raise ValueError("Aerial statistics require team and player references")

        if player_id in player_teams and player_teams[player_id] != team_id:
            raise ValueError("Player belongs to multiple teams")
        player_teams[player_id] = team_id

        grouped[(team_id, player_id)].append(event)

    result: list[PlayerAerialStatistics] = []

    for (team_id, player_id), events in sorted(grouped.items()):
        counts = Counter(event.outcome for event in events)
        summary = PlayerAerialStatistics(
            provider=provider,
            external_match_id=match_id,
            external_team_id=team_id,
            external_player_id=player_id,
            won=counts["won"],
            lost=counts["lost"],
            external_event_ids=tuple(event.external_event_id for event in events),
        )

        if summary.contested != len(events):
            raise ValueError("Aerial totals differ from source event count")

        result.append(summary)

    return result
