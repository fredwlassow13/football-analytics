import pytest

from football_analytics.ingestion.shot_statistics import aggregate_player_shots
from football_analytics.integrations.base import MatchShot


def make_shot(
    index: int,
    *,
    outcome: str = "goal",
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


def test_counts_all_categories_and_derives_totals() -> None:
    outcomes = [
        "goal",
        "saved",
        "saved_to_post",
        "saved_off_target",
        "blocked",
        "off_target",
        "post",
        "wayward",
    ]
    shots = [make_shot(index, outcome=outcome) for index, outcome in enumerate(outcomes, start=1)]

    result = aggregate_player_shots(shots)

    assert len(result) == 1
    player = result[0]
    assert player.provider == "statsbomb"
    assert player.external_match_id == "3869685"
    assert player.external_team_id == "team-1"
    assert player.external_player_id == "player-1"
    assert player.goals == 1
    assert player.saved == 1
    assert player.saved_to_post == 1
    assert player.saved_off_target == 1
    assert player.blocked == 1
    assert player.off_target == 1
    assert player.post == 1
    assert player.wayward == 1
    assert player.shots == 8
    assert player.shots_on_target == 3


def test_separates_players_and_teams_and_reconciles_totals() -> None:
    shots = [
        make_shot(1),
        make_shot(2, outcome="saved"),
        make_shot(3, player_id="player-2", outcome="blocked"),
        make_shot(
            4,
            player_id="player-3",
            team_id="team-2",
            outcome="off_target",
        ),
    ]

    result = aggregate_player_shots(shots)
    by_player = {item.external_player_id: item for item in result}

    assert len(result) == 3
    assert by_player["player-1"].shots == 2
    assert by_player["player-1"].shots_on_target == 2
    assert by_player["player-2"].blocked == 1
    assert by_player["player-3"].external_team_id == "team-2"
    assert sum(item.shots for item in result) == 4
    assert sum(item.goals for item in result) == 1
    assert sum(item.shots_on_target for item in result) == 2


def test_includes_extra_time_and_excludes_shootout() -> None:
    shots = [
        make_shot(1, period=1),
        make_shot(2, period=2),
        make_shot(3, period=3),
        make_shot(4, period=4),
        make_shot(5, period=5),
    ]

    result = aggregate_player_shots(shots)

    assert result[0].shots == 4
    assert result[0].goals == 4


def test_returns_no_players_for_shootout_only() -> None:
    assert aggregate_player_shots([make_shot(1, period=5)]) == []


def test_returns_no_players_for_empty_input() -> None:
    assert aggregate_player_shots([]) == []


def test_rejects_duplicate_events() -> None:
    event = make_shot(1)

    with pytest.raises(ValueError, match="Duplicate shot event"):
        aggregate_player_shots([event, event])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "another-provider"),
        ("external_match_id", "another-match"),
    ],
)
def test_rejects_mixed_sources_or_matches(field: str, value: str) -> None:
    data = make_shot(2).model_dump()
    data[field] = value

    with pytest.raises(ValueError, match="one provider and match"):
        aggregate_player_shots(
            [
                make_shot(1),
                MatchShot.model_validate(data),
            ]
        )


@pytest.mark.parametrize(
    "field",
    ["external_team_id", "external_player_id"],
)
def test_rejects_missing_references(field: str) -> None:
    data = make_shot(1).model_dump()
    data[field] = None

    with pytest.raises(ValueError, match="team and player references"):
        aggregate_player_shots([MatchShot.model_validate(data)])


def test_rejects_player_assigned_to_two_teams() -> None:
    shots = [
        make_shot(1),
        make_shot(2, team_id="team-2"),
    ]

    with pytest.raises(ValueError, match="multiple teams"):
        aggregate_player_shots(shots)


def test_rejects_unsupported_period() -> None:
    with pytest.raises(ValueError, match="Unsupported shot period"):
        aggregate_player_shots([make_shot(1, period=6)])
