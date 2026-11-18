import pytest

from football_analytics.ingestion.own_goal_evidence import (
    collect_own_goal_evidence,
)
from football_analytics.integrations.base import MatchEvent


def event(index: int, **changes: object) -> MatchEvent:
    return MatchEvent.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "match-1",
            "external_event_id": f"event-{index}",
            "source_index": index,
            "period": 1,
            "timestamp_ms": index * 1000,
            "event_type": "Own Goal Against",
            "external_team_id": "team-a",
            "external_player_id": "player-1",
        }
        | changes
    )


def collect(events, team_ids=("team-a", "team-b")):
    return collect_own_goal_evidence(
        events,
        provider="statsbomb",
        match_id="match-1",
        team_ids=team_ids,
    )


@pytest.mark.parametrize(
    "event_type,scoring,conceding",
    [
        ("Own Goal Against", "team-b", "team-a"),
        ("Own Goal For", "team-a", "team-b"),
    ],
)
def test_identifies_scoring_and_conceding_teams(event_type, scoring, conceding) -> None:
    result = collect([event(1, event_type=event_type)])[0]

    assert result.scoring_team_id == scoring
    assert result.conceding_team_id == conceding
    assert result.status == "usable"


def test_preserves_both_perspectives_without_counting_goals() -> None:
    result = collect(
        [
            event(
                2,
                event_type="Own Goal For",
                external_team_id="team-b",
            ),
            event(1),
        ]
    )

    assert [item.external_event_id for item in result] == [
        "event-1",
        "event-2",
    ]
    assert all(item.scoring_team_id == "team-b" for item in result)


def test_ignores_regular_shots_and_shootout_events() -> None:
    result = collect(
        [
            event(1, event_type="Shot"),
            event(2, period=5),
        ]
    )

    assert result == ()


def test_missing_team_requires_review() -> None:
    result = collect([event(1, external_team_id=None)])[0]

    assert result.status == "review"
    assert result.issues == ("missing_team",)
    assert result.scoring_team_id is None
    assert result.conceding_team_id is None


def test_rejects_unrelated_team() -> None:
    with pytest.raises(ValueError, match="another team"):
        collect([event(1, external_team_id="team-c")])


@pytest.mark.parametrize(
    "changes",
    [
        {"external_event_id": "event-1"},
        {"source_index": 1},
    ],
)
def test_rejects_duplicate_sources(changes) -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        collect([event(1), event(2, **changes)])


def test_rejects_another_match() -> None:
    with pytest.raises(ValueError, match="another source or match"):
        collect([event(1, external_match_id="other")])


def test_requires_two_distinct_teams() -> None:
    with pytest.raises(ValueError, match="distinct teams"):
        collect([], team_ids=("team-a", "team-a"))
