from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.goalkeeper_shot_links import (
    GoalkeeperShotPair,
    GoalkeeperShotResolution,
    resolve_goalkeeper_shot_links,
)
from football_analytics.integrations.base import (
    MatchEventRelations,
    MatchGoalkeeperAction,
    MatchShot,
)

type ShotCategory = Literal[
    "goal_conceded",
    "on_target_save",
    "off_target_save",
    "other_shot",
    "review",
]

SHOT_TYPES: dict[str, bool] = {
    "Penalty": True,
    "Open Play": False,
    "Free Kick": False,
    "Corner": False,
    "Kick Off": False,
}

CATEGORY_BY_OUTCOME: dict[str, ShotCategory] = {
    "goal": "goal_conceded",
    "saved": "on_target_save",
    "saved_to_post": "on_target_save",
    "saved_off_target": "off_target_save",
    "blocked": "other_shot",
    "off_target": "other_shot",
    "post": "other_shot",
    "wayward": "other_shot",
}

EXPECTED_KEEPER_TYPES: dict[str, set[str]] = {
    "goal": {"goal_conceded", "penalty_conceded"},
    "saved": {"shot_saved", "penalty_saved"},
    "saved_to_post": {
        "shot_saved_to_post",
        "penalty_saved_to_post",
    },
    "saved_off_target": {"shot_saved_off_target"},
    "blocked": {"shot_faced"},
    "off_target": {"shot_faced"},
    "post": {"shot_faced"},
    "wayward": {"shot_faced"},
}

SHOT_SAVE_TYPES = {
    "shot_saved",
    "penalty_saved",
    "shot_saved_to_post",
    "penalty_saved_to_post",
    "shot_saved_off_target",
}

SAVE_OUTCOMES = {
    "success",
    "in_play",
    "in_play_safe",
    "in_play_danger",
    "saved_twice",
    "touched_out",
    "punched_out",
}


class GoalkeeperShotClassification(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_goalkeeper_event_id: str
    external_shot_event_id: str
    goalkeeper_period: int
    shot_period: int
    goalkeeper_type: str
    goalkeeper_outcome: str | None
    shot_outcome: str
    shot_type: str | None
    is_penalty: bool | None
    category: ShotCategory
    issues: tuple[str, ...]


class GoalkeeperShotClassificationResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    policy_version: Literal["1.0.0"] = "1.0.0"
    resolution: GoalkeeperShotResolution
    classifications: tuple[GoalkeeperShotClassification, ...]


def _classify_pair(
    keeper: MatchGoalkeeperAction,
    shot: MatchShot,
    pair: GoalkeeperShotPair,
    *,
    source_problem_ids: set[str],
) -> GoalkeeperShotClassification:
    issues: list[str] = [str(issue) for issue in pair.issues]

    if (
        keeper.external_event_id in source_problem_ids
        or shot.external_event_id in source_problem_ids
    ):
        issues.append("source_relationship_issue")

    is_penalty: bool | None = None
    if shot.shot_type is None:
        issues.append("missing_shot_type")
    elif shot.shot_type not in SHOT_TYPES:
        issues.append("unsupported_shot_type")
    else:
        is_penalty = SHOT_TYPES[shot.shot_type]

    if shot.period == 5 and is_penalty is False:
        issues.append("shootout_non_penalty")

    expected_types = EXPECTED_KEEPER_TYPES[shot.outcome]

    if is_penalty is not None:
        if shot.outcome == "goal":
            expected_types = {"penalty_conceded" if is_penalty else "goal_conceded"}
        elif shot.outcome == "saved":
            expected_types = {"penalty_saved" if is_penalty else "shot_saved"}
        elif shot.outcome == "saved_to_post":
            expected_types = {"penalty_saved_to_post" if is_penalty else "shot_saved_to_post"}

    if keeper.goalkeeper_type not in expected_types:
        issues.append("goalkeeper_type_shot_outcome_mismatch")

    if keeper.outcome is not None:
        if keeper.goalkeeper_type == "shot_faced":
            issues.append("unexpected_shot_faced_outcome")
        elif keeper.goalkeeper_type in {"goal_conceded", "penalty_conceded"}:
            if keeper.outcome not in {"no_touch", "touched_in"}:
                issues.append("goalkeeper_outcome_mismatch")
        elif keeper.goalkeeper_type in SHOT_SAVE_TYPES:
            if keeper.outcome not in SAVE_OUTCOMES:
                issues.append("goalkeeper_outcome_mismatch")

    category: ShotCategory = "review" if issues else CATEGORY_BY_OUTCOME[shot.outcome]

    return GoalkeeperShotClassification(
        external_goalkeeper_event_id=keeper.external_event_id,
        external_shot_event_id=shot.external_event_id,
        goalkeeper_period=keeper.period,
        shot_period=shot.period,
        goalkeeper_type=keeper.goalkeeper_type,
        goalkeeper_outcome=keeper.outcome,
        shot_outcome=shot.outcome,
        shot_type=shot.shot_type,
        is_penalty=is_penalty,
        category=category,
        issues=tuple(sorted(set(issues))),
    )


def classify_goalkeeper_shots(
    goalkeepers: Sequence[MatchGoalkeeperAction],
    shots: Sequence[MatchShot],
    relations: Sequence[MatchEventRelations],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
) -> GoalkeeperShotClassificationResult:
    resolution = resolve_goalkeeper_shot_links(
        goalkeepers,
        shots,
        relations,
        provider=provider,
        match_id=match_id,
        events_sha256=events_sha256,
    )

    keepers_by_id = {action.external_event_id: action for action in goalkeepers}
    shots_by_id = {action.external_event_id: action for action in shots}
    source_problem_ids = {issue.external_source_event_id for issue in resolution.source_issues}

    return GoalkeeperShotClassificationResult(
        resolution=resolution,
        classifications=tuple(
            _classify_pair(
                keepers_by_id[pair.external_goalkeeper_event_id],
                shots_by_id[pair.external_shot_event_id],
                pair,
                source_problem_ids=source_problem_ids,
            )
            for pair in resolution.pairs
        ),
    )
