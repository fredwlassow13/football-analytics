from datetime import date

import pytest
from test_goal_score_evidence import build as evidence_for
from test_own_goal_evidence import event

from football_analytics.ingestion.score_reconciliation import (
    reconcile_match_score,
)
from football_analytics.integrations.base import Match


def recorded_match(**changes) -> Match:
    return Match.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "match-1",
            "external_competition_id": "competition-1",
            "external_season_id": "season-1",
            "external_home_team_id": "team-a",
            "external_away_team_id": "team-b",
            "match_date": date(2022, 1, 1),
            "home_score": 1,
            "away_score": 0,
        }
        | changes
    )


def one_goal():
    return evidence_for([event(1, event_type="Shot")])


def test_matching_scores_are_confirmed() -> None:
    result = reconcile_match_score(one_goal(), recorded_match())

    assert result.status == "matched"
    assert result.issues == ()
    assert [team.difference for team in result.teams] == [0, 0]


def test_mismatch_preserves_both_scores_and_difference() -> None:
    result = reconcile_match_score(
        one_goal(),
        recorded_match(home_score=2),
    )

    assert result.status == "mismatch"
    assert result.teams[0].recorded_goals == 2
    assert result.teams[0].reconstructed_goals == 1
    assert result.teams[0].difference == -1
    assert "recorded_score_mismatch" in result.issues


def test_missing_recorded_score_requires_review() -> None:
    result = reconcile_match_score(
        one_goal(),
        recorded_match(home_score=None),
    )

    assert result.status == "review"
    assert "missing_recorded_score" in result.issues
    assert all(team.difference is None for team in result.teams)


def test_unresolved_own_goal_does_not_produce_false_match() -> None:
    result = reconcile_match_score(
        evidence_for([event(1)]),
        recorded_match(home_score=0),
    )

    assert result.status == "review"
    assert "unresolved_goal_evidence" in result.issues
    assert all(team.reconstructed_goals is None for team in result.teams)


@pytest.mark.parametrize(
    "changes",
    [
        {"external_match_id": "other"},
        {"provider": "other"},
    ],
)
def test_rejects_different_source_identity(changes) -> None:
    with pytest.raises(ValueError, match="different matches"):
        reconcile_match_score(one_goal(), recorded_match(**changes))


def test_rejects_different_match_teams() -> None:
    with pytest.raises(ValueError, match="teams differ"):
        reconcile_match_score(
            one_goal(),
            recorded_match(external_away_team_id="team-c"),
        )
