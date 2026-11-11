import pytest

from football_analytics.ingestion.team_shot_statistics import (
    aggregate_team_shots,
)
from football_analytics.integrations.base import MatchShot


def make_shot(
    index: int,
    *,
    outcome: str = "saved",
    player_id: str = "player-1",
    team_id: str = "team-1",
    period: int = 1,
) -> MatchShot:
    return MatchShot.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_event_id": f"shot-{index}",
            "source_index": index,
            "period": period,
            "timestamp_ms": index * 1000,
            "external_team_id": team_id,
            "external_player_id": player_id,
            "outcome": outcome,
        }
    )


@pytest.mark.parametrize(
    ("outcome", "on_target"),
    [
        ("goal", 1),
        ("saved", 1),
        ("saved_to_post", 1),
        ("saved_off_target", 0),
        ("blocked", 0),
        ("off_target", 0),
        ("post", 0),
        ("wayward", 0),
    ],
)
def test_preserves_outcome_and_on_target_definition(
    outcome: str,
    on_target: int,
) -> None:
    team = aggregate_team_shots([make_shot(1, outcome=outcome)])[0]

    assert team.shots == 1
    assert team.shots_on_target == on_target
    category = "goals" if outcome == "goal" else outcome
    assert getattr(team, category) == 1


def test_combines_players_and_preserves_ordered_sources() -> None:
    team = aggregate_team_shots(
        [
            make_shot(3, player_id="player-2", outcome="blocked"),
            make_shot(1, outcome="goal"),
            make_shot(2, outcome="saved"),
        ]
    )[0]

    assert team.provider == "statsbomb"
    assert team.external_match_id == "3869685"
    assert team.external_team_id == "team-1"
    assert team.shots == 3
    assert team.goals == 1
    assert team.shots_on_target == 2
    assert team.blocked == 1
    assert team.external_event_ids == ("shot-1", "shot-2", "shot-3")


def test_separates_teams() -> None:
    result = aggregate_team_shots(
        [
            make_shot(1, outcome="goal"),
            make_shot(2, player_id="player-2", team_id="team-2"),
        ]
    )

    by_team = {team.external_team_id: team for team in result}

    assert len(result) == 2
    assert by_team["team-1"].goals == 1
    assert by_team["team-2"].goals == 0
    assert by_team["team-2"].saved == 1
    assert sum(team.shots for team in result) == 2


def test_includes_extra_time_and_excludes_shootout() -> None:
    team = aggregate_team_shots(
        [
            make_shot(1, period=3),
            make_shot(2, period=4),
            make_shot(3, period=5, outcome="goal"),
        ]
    )[0]

    assert team.shots == 2
    assert team.goals == 0
    assert team.external_event_ids == ("shot-1", "shot-2")


def test_rejects_duplicate_events() -> None:
    event = make_shot(1)

    with pytest.raises(ValueError, match="Duplicate shot event"):
        aggregate_team_shots([event, event])


@pytest.mark.parametrize("shootout_only", [False, True])
def test_returns_no_observed_teams_without_playing_shots(
    shootout_only: bool,
) -> None:
    shots = [make_shot(1, period=5)] if shootout_only else []

    assert aggregate_team_shots(shots) == []
