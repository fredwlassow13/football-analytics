from collections import defaultdict
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.player_goalkeeper_statistics import (
    GoalkeeperActionBreakdown,
    GoalkeeperShotCounts,
    PlayerGoalkeeperStatistics,
    PlayerGoalkeeperStatisticsResult,
    aggregate_player_goalkeeper_statistics,
)
from football_analytics.integrations.base import (
    MatchEventRelations,
    MatchGoalkeeperAction,
    MatchShot,
)


class TeamGoalkeeperStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_team_id: str
    external_player_ids: tuple[str, ...]
    counts: GoalkeeperShotCounts
    penalty_counts: GoalkeeperShotCounts
    non_penalty_counts: GoalkeeperShotCounts
    unknown_penalty_counts: GoalkeeperShotCounts
    actions: tuple[GoalkeeperActionBreakdown, ...]
    goalkeeper_event_ids: tuple[str, ...]
    candidate_shot_event_ids: tuple[str, ...]
    unlinked_goalkeeper_event_ids: tuple[str, ...]


class GoalkeeperStatisticsResult(PlayerGoalkeeperStatisticsResult):
    teams: tuple[TeamGoalkeeperStatistics, ...]


def _sum_counts(
    items: Sequence[GoalkeeperShotCounts],
) -> GoalkeeperShotCounts:
    return GoalkeeperShotCounts(
        pair_count=sum(item.pair_count for item in items),
        shot_goals_conceded=sum(item.shot_goals_conceded for item in items),
        on_target_saves=sum(item.on_target_saves for item in items),
        off_target_saves=sum(item.off_target_saves for item in items),
        other_shots=sum(item.other_shots for item in items),
        review_pairs=sum(item.review_pairs for item in items),
    )


def _merge_actions(
    players: Sequence[PlayerGoalkeeperStatistics],
    source_order: dict[str, int],
) -> tuple[GoalkeeperActionBreakdown, ...]:
    grouped: dict[tuple[str, str | None], list[str]] = defaultdict(list)

    for player in players:
        for action in player.actions:
            grouped[(action.goalkeeper_type, action.outcome)].extend(action.external_event_ids)

    return tuple(
        GoalkeeperActionBreakdown(
            goalkeeper_type=goalkeeper_type,
            outcome=outcome,
            event_count=len(event_ids),
            external_event_ids=tuple(
                sorted(event_ids, key=lambda event_id: source_order[event_id])
            ),
        )
        for (goalkeeper_type, outcome), event_ids in sorted(
            grouped.items(),
            key=lambda item: (item[0][0], item[0][1] or ""),
        )
    )


def build_goalkeeper_statistics(
    goalkeepers: Sequence[MatchGoalkeeperAction],
    shots: Sequence[MatchShot],
    relations: Sequence[MatchEventRelations],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None = None,
) -> GoalkeeperStatisticsResult:
    individual = aggregate_player_goalkeeper_statistics(
        goalkeepers,
        shots,
        relations,
        provider=provider,
        match_id=match_id,
        events_sha256=events_sha256,
        available_periods=available_periods,
        periods=periods,
    )

    keeper_order = {action.external_event_id: action.source_index for action in goalkeepers}
    shot_order = {shot.external_event_id: shot.source_index for shot in shots}
    teams: list[TeamGoalkeeperStatistics] = []

    for team_id in sorted({player.external_team_id for player in individual.players}):
        players = [player for player in individual.players if player.external_team_id == team_id]
        keeper_ids = {event_id for player in players for event_id in player.goalkeeper_event_ids}
        shot_ids = {event_id for player in players for event_id in player.candidate_shot_event_ids}
        unlinked_ids = {
            event_id for player in players for event_id in player.unlinked_goalkeeper_event_ids
        }

        teams.append(
            TeamGoalkeeperStatistics(
                external_team_id=team_id,
                external_player_ids=tuple(sorted(player.external_player_id for player in players)),
                counts=_sum_counts([player.counts for player in players]),
                penalty_counts=_sum_counts([player.penalty_counts for player in players]),
                non_penalty_counts=_sum_counts([player.non_penalty_counts for player in players]),
                unknown_penalty_counts=_sum_counts(
                    [player.unknown_penalty_counts for player in players]
                ),
                actions=_merge_actions(players, keeper_order),
                goalkeeper_event_ids=tuple(
                    sorted(keeper_ids, key=lambda event_id: keeper_order[event_id])
                ),
                candidate_shot_event_ids=tuple(
                    sorted(shot_ids, key=lambda event_id: shot_order[event_id])
                ),
                unlinked_goalkeeper_event_ids=tuple(
                    sorted(unlinked_ids, key=lambda event_id: keeper_order[event_id])
                ),
            )
        )

    return GoalkeeperStatisticsResult.model_validate(
        individual.model_dump() | {"teams": tuple(teams)}
    )
