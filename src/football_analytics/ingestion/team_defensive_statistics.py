from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.defensive_scope import DefensiveEvent
from football_analytics.ingestion.defensive_statistics import (
    DefensiveActionStatistics,
    aggregate_player_defensive_actions,
    summarize_defensive_actions,
)


class TeamDefensiveStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    external_team_id: str
    policy_version: Literal["1.0.0"] = "1.0.0"
    population_basis: Literal["observed_source_events"] = "observed_source_events"
    periods: tuple[int, ...]
    event_count: int = Field(gt=0)
    actions: tuple[DefensiveActionStatistics, ...]
    external_event_ids: tuple[str, ...]


def aggregate_team_defensive_actions(
    events: Sequence[DefensiveEvent],
    *,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None = None,
) -> list[TeamDefensiveStatistics]:
    players = aggregate_player_defensive_actions(
        events,
        available_periods=available_periods,
        periods=periods,
    )
    if not players:
        return []

    ordered_events = sorted(events, key=lambda item: item.source_index)
    result: list[TeamDefensiveStatistics] = []

    for team_id in sorted({player.external_team_id for player in players}):
        team_players = [player for player in players if player.external_team_id == team_id]
        selected_ids = {
            event_id for player in team_players for event_id in player.external_event_ids
        }
        team_events = [event for event in ordered_events if event.external_event_id in selected_ids]

        expected_count = sum(player.event_count for player in team_players)
        if len(team_events) != expected_count:
            raise ValueError("Team event count differs from player totals")

        result.append(
            TeamDefensiveStatistics(
                provider=team_players[0].provider,
                external_match_id=team_players[0].external_match_id,
                external_team_id=team_id,
                periods=team_players[0].periods,
                event_count=len(team_events),
                actions=summarize_defensive_actions(team_events),
                external_event_ids=tuple(event.external_event_id for event in team_events),
            )
        )

    return result
