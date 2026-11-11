from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.integrations.base import MatchPass


class PlayerPassStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    provider: str
    external_match_id: str
    external_team_id: str
    external_player_id: str
    completed: int = Field(ge=0)
    incomplete: int = Field(ge=0)
    out: int = Field(ge=0)
    offside: int = Field(ge=0)
    unknown: int = Field(ge=0)

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


def aggregate_player_passes(
    passes: list[MatchPass],
) -> list[PlayerPassStatistics]:
    identities = {(event.provider, event.external_match_id) for event in passes}

    if len(identities) > 1:
        raise ValueError("Passes must belong to one provider and match")

    if not passes:
        return []

    provider, match_id = next(iter(identities))
    seen_ids: set[str] = set()
    player_teams: dict[str, str] = {}
    grouped: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    for event in passes:
        if event.external_event_id in seen_ids:
            raise ValueError(f"Duplicate pass event: {event.external_event_id}")

        seen_ids.add(event.external_event_id)

        if event.period not in {1, 2, 3, 4, 5}:
            raise ValueError(f"Unsupported pass period: {event.period}")

        if event.period == 5:
            continue

        team_id = event.external_team_id
        player_id = event.external_player_id

        if team_id is None or player_id is None:
            raise ValueError("Pass statistics require team and player references")

        previous_team = player_teams.get(player_id)

        if previous_team is not None and previous_team != team_id:
            raise ValueError(f"Player belongs to multiple teams: {player_id}")

        player_teams[player_id] = team_id
        grouped[(team_id, player_id)][event.outcome] += 1

    return [
        PlayerPassStatistics(
            provider=provider,
            external_match_id=match_id,
            external_team_id=team_id,
            external_player_id=player_id,
            completed=counts["complete"],
            incomplete=counts["incomplete"],
            out=counts["out"],
            offside=counts["offside"],
            unknown=counts["unknown"],
        )
        for (team_id, player_id), counts in sorted(grouped.items())
    ]
