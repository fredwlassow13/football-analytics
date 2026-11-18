from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.creation_links import (
    CreationAction,
    CreationLinkResolution,
    CreationPair,
    resolve_creation_links,
)
from football_analytics.integrations.base import (
    MatchEvent,
    MatchPassCreation,
    MatchShotCreation,
)

KNOWN_SHOT_TYPES = {
    "Open Play",
    "Free Kick",
    "Corner",
    "Kick Off",
    "Penalty",
}


class CreationClassification(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_pass_event_id: str
    external_shot_event_id: str
    pass_period: int = Field(ge=1, le=5)
    shot_period: int = Field(ge=1, le=5)
    pass_outcome: str
    shot_outcome: str
    shot_type: str | None
    shot_assist: bool | None
    goal_assist: bool | None
    status: Literal["usable", "review"]
    issues: tuple[str, ...]


class CreationClassificationResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    policy_version: Literal["1.0.0"] = "1.0.0"
    resolution: CreationLinkResolution
    classifications: tuple[CreationClassification, ...]


def _classify_pair(
    pass_action: MatchPassCreation,
    shot_action: MatchShotCreation,
    pair: CreationPair,
    *,
    reference_problem_ids: set[str],
) -> CreationClassification:
    issues: list[str] = [str(issue) for issue in pair.issues]

    if (
        pass_action.external_event_id in reference_problem_ids
        or shot_action.external_event_id in reference_problem_ids
    ):
        issues.append("source_reference_issue")

    if pass_action.outcome != "complete":
        issues.append("pass_not_complete")

    if shot_action.shot_type is None:
        issues.append("missing_shot_type")
    elif shot_action.shot_type not in KNOWN_SHOT_TYPES:
        issues.append("unsupported_shot_type")
    elif shot_action.shot_type == "Penalty":
        issues.append("penalty_creation_not_defined")

    if pass_action.period == 5 or shot_action.period == 5:
        issues.append("shootout_creation_not_defined")

    if pass_action.goal_assist is True and shot_action.outcome != "goal":
        issues.append("goal_assist_without_goal")

    return CreationClassification(
        external_pass_event_id=pass_action.external_event_id,
        external_shot_event_id=shot_action.external_event_id,
        pass_period=pass_action.period,
        shot_period=shot_action.period,
        pass_outcome=pass_action.outcome,
        shot_outcome=shot_action.outcome,
        shot_type=shot_action.shot_type,
        shot_assist=pass_action.shot_assist,
        goal_assist=pass_action.goal_assist,
        status="review" if issues else "usable",
        issues=tuple(sorted(set(issues))),
    )


def classify_creation_links(
    actions: Sequence[CreationAction],
    catalogue: Sequence[MatchEvent],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
) -> CreationClassificationResult:
    resolution = resolve_creation_links(
        actions,
        catalogue,
        provider=provider,
        match_id=match_id,
        events_sha256=events_sha256,
    )

    passes_by_id = {
        action.external_event_id: action
        for action in actions
        if isinstance(action, MatchPassCreation)
    }
    shots_by_id = {
        action.external_event_id: action
        for action in actions
        if isinstance(action, MatchShotCreation)
    }
    reference_problem_ids = {
        issue.external_source_event_id for issue in resolution.reference_issues
    }

    return CreationClassificationResult(
        resolution=resolution,
        classifications=tuple(
            _classify_pair(
                passes_by_id[pair.external_pass_event_id],
                shots_by_id[pair.external_shot_event_id],
                pair,
                reference_problem_ids=reference_problem_ids,
            )
            for pair in resolution.pairs
        ),
    )
