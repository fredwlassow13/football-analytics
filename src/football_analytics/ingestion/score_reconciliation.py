from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.goal_score_evidence import (
    GoalScoreEvidence,
)
from football_analytics.integrations.base import Match


class TeamScoreComparison(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_team_id: str
    recorded_goals: int | None = Field(ge=0)
    reconstructed_goals: int | None = Field(ge=0)
    difference: int | None


class ScoreReconciliation(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    evidence: GoalScoreEvidence
    recorded_score_basis: Literal["match_without_shootout"] = "match_without_shootout"
    status: Literal["matched", "mismatch", "review"]
    issues: tuple[str, ...]
    teams: tuple[TeamScoreComparison, ...]


def reconcile_match_score(
    evidence: GoalScoreEvidence,
    match: Match,
) -> ScoreReconciliation:
    """Compare against recorded match goals, excluding the shootout."""
    if (evidence.provider, evidence.external_match_id) != (
        match.provider,
        match.external_match_id,
    ):
        raise ValueError("Score sources belong to different matches")

    team_ids = (
        match.external_home_team_id,
        match.external_away_team_id,
    )

    if not all(team_ids) or len(set(team_ids)) != 2:
        raise ValueError("Two distinct match teams are required")

    reconstructed = {team.external_team_id: team.reconstructed_goals for team in evidence.teams}

    if len(reconstructed) != len(evidence.teams) or set(reconstructed) != set(team_ids):
        raise ValueError("Score evidence teams differ from match teams")

    recorded = {
        match.external_home_team_id: match.home_score,
        match.external_away_team_id: match.away_score,
    }

    if any(value is not None and value < 0 for value in recorded.values()):
        raise ValueError("Recorded goals cannot be negative")

    issues = set(evidence.issues)

    if evidence.status != "resolved":
        issues.add("unresolved_goal_evidence")

    if any(value is None for value in reconstructed.values()):
        issues.add("missing_reconstructed_score")

    if any(value is None for value in recorded.values()):
        issues.add("missing_recorded_score")

    comparisons: list[TeamScoreComparison] = []

    for team_id in team_ids:
        actual = reconstructed[team_id]
        expected = recorded[team_id]

        difference = None
        if not issues and actual is not None and expected is not None:
            difference = actual - expected

        comparisons.append(
            TeamScoreComparison(
                external_team_id=team_id,
                recorded_goals=expected,
                reconstructed_goals=actual,
                difference=difference,
            )
        )

    status: Literal["matched", "mismatch", "review"]
    if issues:
        status = "review"
    elif any(team.difference != 0 for team in comparisons):
        status = "mismatch"
        issues.add("recorded_score_mismatch")
    else:
        status = "matched"

    return ScoreReconciliation(
        evidence=evidence,
        status=status,
        issues=tuple(sorted(issues)),
        teams=tuple(comparisons),
    )
