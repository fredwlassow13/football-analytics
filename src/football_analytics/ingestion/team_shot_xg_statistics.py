from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.shot_xg_statistics import (
    ShotXgTotals,
    aggregate_player_shot_xg,
)
from football_analytics.integrations.base import MatchShot


class TeamShotXgStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    external_team_id: str
    overall: ShotXgTotals
    penalties: ShotXgTotals
    non_penalties: ShotXgTotals
    unclassified_type: ShotXgTotals
    external_event_ids: tuple[str, ...]


def _merge_xg_totals(items: Sequence[ShotXgTotals]) -> ShotXgTotals:
    available_values = [
        item.available_xg_sum for item in items if item.available_xg_sum is not None
    ]

    return ShotXgTotals(
        shots=sum(item.shots for item in items),
        goals=sum(item.goals for item in items),
        shots_with_xg=sum(item.shots_with_xg for item in items),
        available_xg_sum=(sum(available_values, Decimal(0)) if available_values else None),
    )


def aggregate_team_shot_xg(
    shots: list[MatchShot],
) -> list[TeamShotXgStatistics]:
    players = aggregate_player_shot_xg(shots)
    if not players:
        return []

    result: list[TeamShotXgStatistics] = []

    for team_id in sorted({player.external_team_id for player in players}):
        team_players = [player for player in players if player.external_team_id == team_id]
        event_ids = tuple(
            event.external_event_id
            for event in sorted(shots, key=lambda item: item.source_index)
            if event.period in {1, 2, 3, 4} and event.external_team_id == team_id
        )

        summary = TeamShotXgStatistics(
            provider=team_players[0].provider,
            external_match_id=team_players[0].external_match_id,
            external_team_id=team_id,
            overall=_merge_xg_totals([player.overall for player in team_players]),
            penalties=_merge_xg_totals([player.penalties for player in team_players]),
            non_penalties=_merge_xg_totals([player.non_penalties for player in team_players]),
            unclassified_type=_merge_xg_totals(
                [player.unclassified_type for player in team_players]
            ),
            external_event_ids=event_ids,
        )

        if summary.overall.shots != len(event_ids):
            raise ValueError("Team xG shot count differs from source event count")

        result.append(summary)

    return result
