from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.creation_classification import (
    CreationClassification,
    classify_creation_links,
)
from football_analytics.ingestion.creation_links import (
    CreationAction,
    CreationReferenceIssue,
)
from football_analytics.ingestion.offensive_scope import select_offensive_periods
from football_analytics.integrations.base import (
    MatchEvent,
    MatchPassCreation,
    MatchShotCreation,
)


class CreationFlagCounts(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    true: int = Field(ge=0)
    false: int = Field(ge=0)
    unknown: int = Field(ge=0)


class CreationCounts(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    pass_count: int = Field(ge=0)
    candidate_pairs: int = Field(ge=0)
    usable_pairs: int = Field(ge=0)
    review_pairs: int = Field(ge=0)
    recorded_shot_assists: int = Field(ge=0)
    recorded_goal_assists: int = Field(ge=0)
    passes_with_any_recorded_flag: int = Field(ge=0)
    passes_with_both_recorded_flags: int = Field(ge=0)
    usable_pairs_without_positive_flag: int = Field(ge=0)


class CreationStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    counts: CreationCounts
    shot_assist_flags: CreationFlagCounts
    goal_assist_flags: CreationFlagCounts
    pass_event_ids: tuple[str, ...]
    candidate_shot_event_ids: tuple[str, ...]
    usable_pass_event_ids: tuple[str, ...]
    shot_assist_pass_event_ids: tuple[str, ...]
    goal_assist_pass_event_ids: tuple[str, ...]
    unlinked_pass_event_ids: tuple[str, ...]
    flagged_unlinked_pass_event_ids: tuple[str, ...]


class PlayerCreationStatistics(CreationStatistics):
    external_team_id: str
    external_player_id: str


class TeamCreationStatistics(CreationStatistics):
    external_team_id: str
    external_player_ids: tuple[str, ...]


class CreationStatisticsResult(CreationStatistics):
    provider: str
    external_match_id: str
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    formula_version: Literal["1.0.0"] = "1.0.0"
    classification_policy_version: Literal["1.0.0"] = "1.0.0"
    link_policy_version: Literal["1.0.0"] = "1.0.0"
    population_basis: Literal["observed_passes"] = "observed_passes"
    scope_basis: Literal["pass_period"] = "pass_period"
    available_periods: tuple[int, ...]
    periods: tuple[int, ...]
    players: tuple[PlayerCreationStatistics, ...]
    teams: tuple[TeamCreationStatistics, ...]
    shot_event_ids: tuple[str, ...]
    unlinked_shot_event_ids: tuple[str, ...]
    review_pairs: tuple[CreationClassification, ...]
    cross_scope_review_pairs: tuple[CreationClassification, ...]
    reference_issues: tuple[CreationReferenceIssue, ...]
    excluded_shootout_creation_event_ids: tuple[str, ...]


def _flag_counts(values: Sequence[bool | None]) -> CreationFlagCounts:
    return CreationFlagCounts(
        true=sum(value is True for value in values),
        false=sum(value is False for value in values),
        unknown=sum(value is None for value in values),
    )


def _summarize_passes(
    passes: Sequence[MatchPassCreation],
    classifications: Sequence[CreationClassification],
    shot_order: dict[str, int],
) -> CreationStatistics:
    pass_ids = {action.external_event_id for action in passes}
    rows = [row for row in classifications if row.external_pass_event_id in pass_ids]
    usable = [row for row in rows if row.status == "usable"]

    candidate_pass_ids = {row.external_pass_event_id for row in rows}
    candidate_shot_ids = {row.external_shot_event_id for row in rows}
    usable_pass_ids = {row.external_pass_event_id for row in usable}
    shot_assist_pass_ids = {row.external_pass_event_id for row in usable if row.shot_assist is True}
    goal_assist_pass_ids = {row.external_pass_event_id for row in usable if row.goal_assist is True}

    def ordered_pass_ids(ids: set[str]) -> tuple[str, ...]:
        return tuple(
            action.external_event_id for action in passes if action.external_event_id in ids
        )

    return CreationStatistics(
        counts=CreationCounts(
            pass_count=len(passes),
            candidate_pairs=len(rows),
            usable_pairs=len(usable),
            review_pairs=len(rows) - len(usable),
            recorded_shot_assists=len(shot_assist_pass_ids),
            recorded_goal_assists=len(goal_assist_pass_ids),
            passes_with_any_recorded_flag=len(shot_assist_pass_ids | goal_assist_pass_ids),
            passes_with_both_recorded_flags=len(shot_assist_pass_ids & goal_assist_pass_ids),
            usable_pairs_without_positive_flag=sum(
                row.shot_assist is not True and row.goal_assist is not True for row in usable
            ),
        ),
        shot_assist_flags=_flag_counts([action.shot_assist for action in passes]),
        goal_assist_flags=_flag_counts([action.goal_assist for action in passes]),
        pass_event_ids=tuple(action.external_event_id for action in passes),
        candidate_shot_event_ids=tuple(
            sorted(
                candidate_shot_ids,
                key=lambda event_id: shot_order[event_id],
            )
        ),
        usable_pass_event_ids=ordered_pass_ids(usable_pass_ids),
        shot_assist_pass_event_ids=ordered_pass_ids(shot_assist_pass_ids),
        goal_assist_pass_event_ids=ordered_pass_ids(goal_assist_pass_ids),
        unlinked_pass_event_ids=ordered_pass_ids(pass_ids - candidate_pass_ids),
        flagged_unlinked_pass_event_ids=tuple(
            action.external_event_id
            for action in passes
            if action.external_event_id not in candidate_pass_ids
            and (action.shot_assist is True or action.goal_assist is True)
        ),
    )


def build_creation_statistics(
    actions: Sequence[CreationAction],
    catalogue: Sequence[MatchEvent],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None = None,
) -> CreationStatisticsResult:
    selected = select_offensive_periods(
        actions,
        available_periods=available_periods,
        periods=periods,
    )
    classification = classify_creation_links(
        actions,
        catalogue,
        provider=provider,
        match_id=match_id,
        events_sha256=events_sha256,
    )

    if any(entry.period not in {*available_periods, 5} for entry in catalogue):
        raise ValueError("Catalogue period is incompatible with available periods")

    selected_periods = tuple(sorted(available_periods if periods is None else periods))
    selected_passes = [action for action in selected if isinstance(action, MatchPassCreation)]
    selected_shots = [action for action in selected if isinstance(action, MatchShotCreation)]
    selected_pass_ids = {action.external_event_id for action in selected_passes}
    selected_shot_ids = {action.external_event_id for action in selected_shots}
    selected_ids = selected_pass_ids | selected_shot_ids
    shot_order = {
        action.external_event_id: action.source_index
        for action in actions
        if isinstance(action, MatchShotCreation)
    }

    player_groups: dict[tuple[str, str], list[MatchPassCreation]] = defaultdict(list)
    team_groups: dict[str, list[MatchPassCreation]] = defaultdict(list)
    player_teams: dict[str, str] = {}

    for action in selected_passes:
        team_id = action.external_team_id
        player_id = action.external_player_id

        if team_id is None or player_id is None:
            raise ValueError("Creation statistics require passer team and player")

        if player_id in player_teams and player_teams[player_id] != team_id:
            raise ValueError("Passer belongs to multiple teams")

        player_teams[player_id] = team_id
        player_groups[(team_id, player_id)].append(action)
        team_groups[team_id].append(action)

    total = _summarize_passes(selected_passes, classification.classifications, shot_order)
    unlinked_shots = set(classification.resolution.unlinked_shot_event_ids)

    return CreationStatisticsResult.model_validate(
        total.model_dump()
        | {
            "provider": provider,
            "external_match_id": match_id,
            "events_sha256": events_sha256,
            "classification_policy_version": classification.policy_version,
            "link_policy_version": classification.resolution.policy_version,
            "available_periods": tuple(sorted(available_periods)),
            "periods": selected_periods,
            "players": tuple(
                PlayerCreationStatistics.model_validate(
                    _summarize_passes(
                        group, classification.classifications, shot_order
                    ).model_dump()
                    | {
                        "external_team_id": team_id,
                        "external_player_id": player_id,
                    }
                )
                for (team_id, player_id), group in sorted(player_groups.items())
            ),
            "teams": tuple(
                TeamCreationStatistics.model_validate(
                    _summarize_passes(
                        group, classification.classifications, shot_order
                    ).model_dump()
                    | {
                        "external_team_id": team_id,
                        "external_player_ids": tuple(
                            sorted(
                                player_id
                                for player_id, owner in player_teams.items()
                                if owner == team_id
                            )
                        ),
                    }
                )
                for team_id, group in sorted(team_groups.items())
            ),
            "shot_event_ids": tuple(action.external_event_id for action in selected_shots),
            "unlinked_shot_event_ids": tuple(
                action.external_event_id
                for action in selected_shots
                if action.external_event_id in unlinked_shots
            ),
            "review_pairs": tuple(
                row
                for row in classification.classifications
                if row.status == "review" and row.external_pass_event_id in selected_pass_ids
            ),
            "cross_scope_review_pairs": tuple(
                row
                for row in classification.classifications
                if row.status == "review"
                and row.external_pass_event_id not in selected_pass_ids
                and row.external_shot_event_id in selected_shot_ids
            ),
            "reference_issues": tuple(
                issue
                for issue in classification.resolution.reference_issues
                if issue.external_source_event_id in selected_ids
            ),
            "excluded_shootout_creation_event_ids": tuple(
                action.external_event_id
                for action in sorted(actions, key=lambda item: item.source_index)
                if action.period == 5
            ),
        }
    )
