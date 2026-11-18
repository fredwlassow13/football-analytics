from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.goalkeeper_shot_classification import (
    GoalkeeperShotClassification,
    classify_goalkeeper_shots,
)
from football_analytics.ingestion.goalkeeper_shot_links import RelationshipIssue
from football_analytics.integrations.base import (
    MatchEventRelations,
    MatchGoalkeeperAction,
    MatchShot,
)


class GoalkeeperShotCounts(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    pair_count: int = Field(ge=0)
    shot_goals_conceded: int = Field(ge=0)
    on_target_saves: int = Field(ge=0)
    off_target_saves: int = Field(ge=0)
    other_shots: int = Field(ge=0)
    review_pairs: int = Field(ge=0)


class GoalkeeperActionBreakdown(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    goalkeeper_type: str
    outcome: str | None
    event_count: int = Field(gt=0)
    external_event_ids: tuple[str, ...]


class PlayerGoalkeeperStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_team_id: str
    external_player_id: str
    counts: GoalkeeperShotCounts
    penalty_counts: GoalkeeperShotCounts
    non_penalty_counts: GoalkeeperShotCounts
    unknown_penalty_counts: GoalkeeperShotCounts
    actions: tuple[GoalkeeperActionBreakdown, ...]
    goalkeeper_event_ids: tuple[str, ...]
    candidate_shot_event_ids: tuple[str, ...]
    unlinked_goalkeeper_event_ids: tuple[str, ...]


class PlayerGoalkeeperStatisticsResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    formula_version: Literal["1.0.0"] = "1.0.0"
    classification_policy_version: Literal["1.0.0"] = "1.0.0"
    link_policy_version: Literal["1.0.0"] = "1.0.0"
    population_basis: Literal["observed_goalkeeper_events"] = "observed_goalkeeper_events"
    available_periods: tuple[int, ...]
    periods: tuple[int, ...]
    players: tuple[PlayerGoalkeeperStatistics, ...]
    goalkeeper_event_ids: tuple[str, ...]
    shot_event_ids: tuple[str, ...]
    unlinked_shot_event_ids: tuple[str, ...]
    review_pairs: tuple[GoalkeeperShotClassification, ...]
    source_issues: tuple[RelationshipIssue, ...]


def _select_periods(
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None,
) -> tuple[int, ...]:
    if (
        any(type(period) is not int for period in available_periods)
        or len(available_periods) != len(set(available_periods))
        or set(available_periods)
        not in (
            {1, 2},
            {1, 2, 3, 4},
            {1, 2, 5},
            {1, 2, 3, 4, 5},
        )
    ):
        raise ValueError("Available periods must describe a complete match")

    requested = (
        tuple(period for period in available_periods if period != 5) if periods is None else periods
    )

    if (
        not requested
        or any(type(period) is not int for period in requested)
        or len(requested) != len(set(requested))
        or not set(requested).issubset(available_periods)
    ):
        raise ValueError("Requested periods are invalid or unavailable")

    if 5 in requested and set(requested) != {5}:
        raise ValueError("Shootout must be selected separately")

    return tuple(sorted(requested))


def _counts(
    rows: Sequence[GoalkeeperShotClassification],
) -> GoalkeeperShotCounts:
    counts = Counter(row.category for row in rows)

    return GoalkeeperShotCounts(
        pair_count=len(rows),
        shot_goals_conceded=counts["goal_conceded"],
        on_target_saves=counts["on_target_save"],
        off_target_saves=counts["off_target_save"],
        other_shots=counts["other_shot"],
        review_pairs=counts["review"],
    )


def _action_breakdown(
    actions: Sequence[MatchGoalkeeperAction],
) -> tuple[GoalkeeperActionBreakdown, ...]:
    grouped: dict[tuple[str, str | None], list[MatchGoalkeeperAction]] = defaultdict(list)

    for action in actions:
        grouped[(action.goalkeeper_type, action.outcome)].append(action)

    return tuple(
        GoalkeeperActionBreakdown(
            goalkeeper_type=goalkeeper_type,
            outcome=outcome,
            event_count=len(group),
            external_event_ids=tuple(action.external_event_id for action in group),
        )
        for (goalkeeper_type, outcome), group in sorted(
            grouped.items(),
            key=lambda item: (item[0][0], item[0][1] or ""),
        )
    )


def aggregate_player_goalkeeper_statistics(
    goalkeepers: Sequence[MatchGoalkeeperAction],
    shots: Sequence[MatchShot],
    relations: Sequence[MatchEventRelations],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None = None,
) -> PlayerGoalkeeperStatisticsResult:
    selected_periods = _select_periods(available_periods, periods)

    classification = classify_goalkeeper_shots(
        goalkeepers,
        shots,
        relations,
        provider=provider,
        match_id=match_id,
        events_sha256=events_sha256,
    )

    if any(relation.period not in available_periods for relation in relations):
        raise ValueError("Source period is incompatible with available periods")

    selected_actions = sorted(
        (action for action in goalkeepers if action.period in selected_periods),
        key=lambda item: item.source_index,
    )
    selected_shots = sorted(
        (shot for shot in shots if shot.period in selected_periods),
        key=lambda item: item.source_index,
    )

    rows_by_keeper: dict[str, list[GoalkeeperShotClassification]] = defaultdict(list)
    for row in classification.classifications:
        if row.goalkeeper_period in selected_periods:
            rows_by_keeper[row.external_goalkeeper_event_id].append(row)

    grouped: dict[tuple[str, str], list[MatchGoalkeeperAction]] = defaultdict(list)
    player_teams: dict[str, str] = {}

    for action in selected_actions:
        team_id = action.external_team_id
        player_id = action.external_player_id

        if team_id is None or player_id is None:
            raise ValueError("Goalkeeper statistics require team and player")

        if player_id in player_teams and player_teams[player_id] != team_id:
            raise ValueError("Goalkeeper belongs to multiple teams")

        player_teams[player_id] = team_id
        grouped[(team_id, player_id)].append(action)

    shots_by_id = {shot.external_event_id: shot for shot in shots}
    unlinked_keepers = set(classification.resolution.unlinked_goalkeeper_event_ids)
    unlinked_shots = set(classification.resolution.unlinked_shot_event_ids)
    relations_by_id = {relation.external_event_id: relation for relation in relations}

    players: list[PlayerGoalkeeperStatistics] = []

    for (team_id, player_id), actions in sorted(grouped.items()):
        rows = [row for action in actions for row in rows_by_keeper[action.external_event_id]]
        candidate_shot_ids = {row.external_shot_event_id for row in rows}

        players.append(
            PlayerGoalkeeperStatistics(
                external_team_id=team_id,
                external_player_id=player_id,
                counts=_counts(rows),
                penalty_counts=_counts([row for row in rows if row.is_penalty is True]),
                non_penalty_counts=_counts([row for row in rows if row.is_penalty is False]),
                unknown_penalty_counts=_counts([row for row in rows if row.is_penalty is None]),
                actions=_action_breakdown(actions),
                goalkeeper_event_ids=tuple(action.external_event_id for action in actions),
                candidate_shot_event_ids=tuple(
                    sorted(
                        candidate_shot_ids,
                        key=lambda event_id: shots_by_id[event_id].source_index,
                    )
                ),
                unlinked_goalkeeper_event_ids=tuple(
                    action.external_event_id
                    for action in actions
                    if action.external_event_id in unlinked_keepers
                ),
            )
        )

    return PlayerGoalkeeperStatisticsResult(
        provider=provider,
        external_match_id=match_id,
        events_sha256=events_sha256,
        classification_policy_version=classification.policy_version,
        link_policy_version=classification.resolution.policy_version,
        available_periods=tuple(sorted(available_periods)),
        periods=selected_periods,
        players=tuple(players),
        goalkeeper_event_ids=tuple(action.external_event_id for action in selected_actions),
        shot_event_ids=tuple(shot.external_event_id for shot in selected_shots),
        unlinked_shot_event_ids=tuple(
            shot.external_event_id
            for shot in selected_shots
            if shot.external_event_id in unlinked_shots
        ),
        review_pairs=tuple(
            row
            for row in classification.classifications
            if row.category == "review"
            and (row.goalkeeper_period in selected_periods or row.shot_period in selected_periods)
        ),
        source_issues=tuple(
            issue
            for issue in classification.resolution.source_issues
            if relations_by_id[issue.external_source_event_id].period in selected_periods
        ),
    )
