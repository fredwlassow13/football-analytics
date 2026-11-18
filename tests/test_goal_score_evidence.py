import pytest
from test_own_goal_evidence import event
from test_own_goal_links import opposite, relations_for

from football_analytics.ingestion.goal_score_evidence import (
    build_goal_score_evidence,
)
from football_analytics.integrations.base import MatchEvent, MatchShot


def build(events, links=None, shots=None):
    if shots is None:
        shots = [
            MatchShot.model_validate(
                item.model_dump(include=set(MatchEvent.model_fields)) | {"outcome": "goal"}
            )
            for item in events
            if item.event_type == "Shot"
        ]

    return build_goal_score_evidence(
        events,
        shots,
        relations_for(events, links or {}),
        provider="statsbomb",
        match_id="match-1",
        team_ids=("team-a", "team-b"),
        events_sha256="a" * 64,
    )


def test_combines_shot_goal_and_one_resolved_own_goal() -> None:
    result = build(
        [
            event(1),
            opposite(),
            event(3, event_type="Shot"),
        ],
        {"event-1": ("event-2",)},
    )

    teams = {item.external_team_id: item for item in result.teams}

    assert result.status == "resolved"
    assert teams["team-a"].shot_goals == 1
    assert teams["team-a"].reconstructed_goals == 1
    assert teams["team-b"].resolved_own_goals == 1
    assert teams["team-b"].reconstructed_goals == 1


def test_shootout_goals_do_not_change_match_score() -> None:
    result = build(
        [
            event(1, event_type="Shot"),
            event(2, event_type="Shot", period=5),
        ]
    )

    assert result.teams[0].reconstructed_goals == 1
    assert result.shot_goal_event_ids == ("event-1",)
    assert result.excluded_shootout_shot_ids == ("event-2",)


def test_unpaired_own_goal_blocks_complete_totals() -> None:
    result = build([event(1)])

    assert result.status == "review"
    assert "own_goal_requires_review" in result.issues
    assert all(team.reconstructed_goals is None for team in result.teams)


def test_unlinked_perspectives_are_not_counted_twice() -> None:
    result = build([event(1), opposite()])

    assert result.status == "review"
    assert all(team.resolved_own_goals == 0 for team in result.teams)
    assert all(team.reconstructed_goals is None for team in result.teams)


def test_inconsistent_pair_remains_for_review() -> None:
    own_for = opposite().model_copy(update={"timestamp_ms": 2000})
    result = build(
        [event(1), own_for],
        {"event-1": ("event-2",)},
    )

    assert result.status == "review"
    assert result.own_goal_links.pairs[0].status == "review"


def test_related_shot_goal_and_own_goal_require_review() -> None:
    result = build(
        [event(1), opposite(), event(3, event_type="Shot")],
        {
            "event-1": ("event-2",),
            "event-3": ("event-1",),
        },
    )

    assert result.status == "review"
    assert "shot_goal_related_to_own_goal" in result.issues
    assert all(team.reconstructed_goals is None for team in result.teams)


def test_requires_all_shots_from_catalogue() -> None:
    with pytest.raises(ValueError, match="coverage differs"):
        build([event(1, event_type="Shot")], shots=[])


def test_missing_shot_goal_team_requires_review() -> None:
    result = build(
        [
            event(
                1,
                event_type="Shot",
                external_team_id=None,
            )
        ]
    )

    assert result.status == "review"
    assert "missing_shot_goal_team" in result.issues
