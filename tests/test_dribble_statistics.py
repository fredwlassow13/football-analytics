from decimal import Decimal

import pytest

from football_analytics.ingestion.dribble_statistics import (
    PlayerDribbleStatistics,
    aggregate_player_dribbles,
)
from football_analytics.integrations.base import MatchDribble


def make_dribble(
    index: int,
    *,
    outcome: str = "complete",
    player_id: str = "player-1",
    team_id: str = "team-1",
    period: int = 1,
) -> MatchDribble:
    return MatchDribble.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_event_id": f"dribble-{index}",
            "source_index": index,
            "period": period,
            "timestamp_ms": index * 1000,
            "external_team_id": team_id,
            "external_player_id": player_id,
            "outcome": outcome,
        }
    )


def test_counts_outcomes_and_calculates_percentage() -> None:
    dribbles = [
        make_dribble(1),
        make_dribble(2, outcome="incomplete"),
        make_dribble(3, outcome="incomplete"),
    ]

    result = aggregate_player_dribbles(dribbles)

    assert len(result) == 1
    player = result[0]
    assert player.provider == "statsbomb"
    assert player.external_match_id == "3869685"
    assert player.external_team_id == "team-1"
    assert player.external_player_id == "player-1"
    assert player.completed == 1
    assert player.incomplete == 2
    assert player.attempted == 3
    assert player.completion_pct == Decimal("33.33")


def test_separates_players_and_teams_and_reconciles_totals() -> None:
    dribbles = [
        make_dribble(1),
        make_dribble(2, outcome="incomplete"),
        make_dribble(3, player_id="player-2"),
        make_dribble(
            4,
            player_id="player-3",
            team_id="team-2",
            outcome="incomplete",
        ),
    ]

    result = aggregate_player_dribbles(dribbles)
    by_player = {item.external_player_id: item for item in result}

    assert len(result) == 3
    assert by_player["player-1"].attempted == 2
    assert by_player["player-2"].completed == 1
    assert by_player["player-3"].external_team_id == "team-2"
    assert sum(item.attempted for item in result) == 4
    assert sum(item.completed for item in result) == 2
    assert sum(item.incomplete for item in result) == 2


def test_includes_extra_time_and_excludes_shootout() -> None:
    dribbles = [
        make_dribble(1, period=1),
        make_dribble(2, period=2),
        make_dribble(3, period=3),
        make_dribble(4, period=4),
        make_dribble(5, period=5),
    ]

    result = aggregate_player_dribbles(dribbles)

    assert result[0].attempted == 4


def test_returns_no_players_for_shootout_only() -> None:
    assert aggregate_player_dribbles([make_dribble(1, period=5)]) == []


def test_returns_no_players_for_empty_input() -> None:
    assert aggregate_player_dribbles([]) == []


def test_rejects_duplicate_events() -> None:
    event = make_dribble(1)

    with pytest.raises(ValueError, match="Duplicate dribble event"):
        aggregate_player_dribbles([event, event])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "another-provider"),
        ("external_match_id", "another-match"),
    ],
)
def test_rejects_mixed_sources_or_matches(field: str, value: str) -> None:
    data = make_dribble(2).model_dump()
    data[field] = value

    with pytest.raises(ValueError, match="one provider and match"):
        aggregate_player_dribbles(
            [
                make_dribble(1),
                MatchDribble.model_validate(data),
            ]
        )


@pytest.mark.parametrize(
    "field",
    ["external_team_id", "external_player_id"],
)
def test_rejects_missing_references(field: str) -> None:
    data = make_dribble(1).model_dump()
    data[field] = None

    with pytest.raises(ValueError, match="team and player references"):
        aggregate_player_dribbles([MatchDribble.model_validate(data)])


def test_rejects_player_assigned_to_two_teams() -> None:
    dribbles = [
        make_dribble(1),
        make_dribble(2, team_id="team-2"),
    ]

    with pytest.raises(ValueError, match="multiple teams"):
        aggregate_player_dribbles(dribbles)


def test_rejects_unsupported_period() -> None:
    with pytest.raises(ValueError, match="Unsupported dribble period"):
        aggregate_player_dribbles([make_dribble(1, period=6)])


@pytest.mark.parametrize(
    ("completed", "incomplete", "expected"),
    [
        (0, 0, None),
        (0, 2, Decimal("0.00")),
        (2, 0, Decimal("100.00")),
        (2, 1, Decimal("66.67")),
    ],
)
def test_percentage_boundaries_and_rounding(
    completed: int,
    incomplete: int,
    expected: Decimal | None,
) -> None:
    statistics = PlayerDribbleStatistics(
        provider="statsbomb",
        external_match_id="3869685",
        external_team_id="team-1",
        external_player_id="player-1",
        completed=completed,
        incomplete=incomplete,
    )

    assert statistics.completion_pct == expected
