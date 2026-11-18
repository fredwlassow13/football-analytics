from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.own_goal_links import (
    OwnGoalLinkResult,
    resolve_own_goal_links,
)
from football_analytics.integrations.base import (
    MatchEvent,
    MatchEventRelations,
    MatchShot,
)


class TeamScoreEvidence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_team_id: str
    shot_goals: int = Field(ge=0)
    resolved_own_goals: int = Field(ge=0)
    reconstructed_goals: int | None = Field(ge=0)


class GoalScoreEvidence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: Literal["1.0.0"] = "1.0.0"
    status: Literal["resolved", "review"]
    issues: tuple[str, ...]
    teams: tuple[TeamScoreEvidence, ...]
    shot_goal_event_ids: tuple[str, ...]
    excluded_shootout_shot_ids: tuple[str, ...]
    own_goal_links: OwnGoalLinkResult


def build_goal_score_evidence(
    events: Sequence[MatchEvent],
    shots: Sequence[MatchShot],
    relations: Sequence[MatchEventRelations],
    *,
    provider: str,
    match_id: str,
    team_ids: tuple[str, str],
    events_sha256: str,
) -> GoalScoreEvidence:
    links = resolve_own_goal_links(
        events,
        relations,
        provider=provider,
        match_id=match_id,
        team_ids=team_ids,
    )

    catalogue = {item.external_event_id: item for item in events}
    shots_by_id: dict[str, MatchShot] = {}
    indexes: set[int] = set()

    for shot in shots:
        if shot.external_event_id in shots_by_id or shot.source_index in indexes:
            raise ValueError("Duplicate shot source")

        shots_by_id[shot.external_event_id] = shot
        indexes.add(shot.source_index)

    expected_shot_ids = {key for key, item in catalogue.items() if item.event_type == "Shot"}

    if set(shots_by_id) != expected_shot_ids:
        raise ValueError("Shot and event coverage differs")

    for key, shot in shots_by_id.items():
        if any(
            getattr(shot, field) != getattr(catalogue[key], field)
            for field in MatchEvent.model_fields
        ):
            raise ValueError("Shot and event headers differ")

    ordered_shots = sorted(shots, key=lambda item: item.source_index)
    goals = [shot for shot in ordered_shots if shot.period != 5 and shot.is_goal]

    shot_counts = dict.fromkeys(team_ids, 0)
    own_counts = dict.fromkeys(team_ids, 0)
    issues: set[str] = set()

    for shot in goals:
        if shot.external_team_id is None:
            issues.add("missing_shot_goal_team")
        elif shot.external_team_id not in shot_counts:
            raise ValueError("Shot goal references another team")
        else:
            shot_counts[shot.external_team_id] += 1

    if links.unpaired_event_ids or any(pair.status == "review" for pair in links.pairs):
        issues.add("own_goal_requires_review")

    for pair in links.pairs:
        if pair.status != "usable":
            continue

        scoring_team = catalogue[pair.for_event_id].external_team_id
        if scoring_team is None:
            raise ValueError("Usable own-goal pair has no scoring team")

        own_counts[scoring_team] += 1

    goal_ids = {shot.external_event_id for shot in goals}
    own_ids = {
        key
        for key, item in catalogue.items()
        if item.period != 5 and item.event_type in {"Own Goal For", "Own Goal Against"}
    }

    for relation in relations:
        source_id = relation.external_event_id
        for target_id in relation.related_event_ids or ():
            if (source_id in goal_ids and target_id in own_ids) or (
                source_id in own_ids and target_id in goal_ids
            ):
                issues.add("shot_goal_related_to_own_goal")

    return GoalScoreEvidence(
        provider=provider,
        external_match_id=match_id,
        events_sha256=events_sha256,
        status="review" if issues else "resolved",
        issues=tuple(sorted(issues)),
        teams=tuple(
            TeamScoreEvidence(
                external_team_id=team,
                shot_goals=shot_counts[team],
                resolved_own_goals=own_counts[team],
                reconstructed_goals=(None if issues else shot_counts[team] + own_counts[team]),
            )
            for team in sorted(team_ids)
        ),
        shot_goal_event_ids=tuple(shot.external_event_id for shot in goals),
        excluded_shootout_shot_ids=tuple(
            shot.external_event_id for shot in ordered_shots if shot.period == 5
        ),
        own_goal_links=links,
    )
