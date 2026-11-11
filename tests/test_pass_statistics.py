from decimal import Decimal

import pytest

from football_analytics.ingestion.pass_statistics import aggregate_player_passes
from football_analytics.integrations.base import MatchPass


def make_pass(
    index: int,
    *,
    outcome: str = "complete",
    player_id: str = "player-1",
    team_id: str = "team-1",
    period: int = 1,
) -> MatchPass:
    return MatchPass.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_event_id": f"pass-{index}",
            "source_index": index,
            "period": period,
            "timestamp_ms": index * 1000,
            "external_team_id": team_id,
            "external_player_id": player_id,
            "outcome": outcome,
        }
    )


def test_counts_each_outcome_and_preserves_identity() -> None:
    outcomes = [
        "complete",
        "complete",
        "incomplete",
        "out",
        "offside",
        "unknown",
    ]
    passes = [make_pass(index, outcome=outcome) for index, outcome in enumerate(outcomes, start=1)]

    result = aggregate_player_passes(passes)

    assert len(result) == 1
    player = result[0]
    assert player.provider == "statsbomb"
    assert player.external_match_id == "3869685"
    assert player.external_team_id == "team-1"
    assert player.external_player_id == "player-1"
    assert player.completed == 2
    assert player.incomplete == 1
    assert player.out == 1
    assert player.offside == 1
    assert player.unknown == 1
    assert player.not_completed == 3
    assert player.attempted == 6
    assert player.completion_pct == Decimal("33.33")


def test_separates_players_and_teams_and_reconciles_totals() -> None:
    passes = [
        make_pass(1),
        make_pass(2, outcome="unknown"),
        make_pass(3, player_id="player-2"),
        make_pass(
            4,
            player_id="player-3",
            team_id="team-2",
            outcome="out",
        ),
    ]

    result = aggregate_player_passes(passes)
    by_player = {item.external_player_id: item for item in result}

    assert len(result) == 3
    assert by_player["player-1"].attempted == 2
    assert by_player["player-2"].attempted == 1
    assert by_player["player-3"].external_team_id == "team-2"
    assert sum(item.attempted for item in result) == 4
    assert sum(item.completed for item in result) == 2
    assert sum(item.not_completed for item in result) == 1
    assert sum(item.unknown for item in result) == 1


def test_includes_extra_time_and_excludes_shootout() -> None:
    passes = [
        make_pass(1, period=1),
        make_pass(2, period=2),
        make_pass(3, period=3),
        make_pass(4, period=4),
        make_pass(5, period=5),
    ]

    result = aggregate_player_passes(passes)

    assert result[0].attempted == 4


def test_returns_no_players_for_shootout_only() -> None:
    assert aggregate_player_passes([make_pass(1, period=5)]) == []


def test_returns_no_players_for_empty_input() -> None:
    assert aggregate_player_passes([]) == []


def test_rejects_duplicate_events() -> None:
    event = make_pass(1)

    with pytest.raises(ValueError, match="Duplicate pass event"):
        aggregate_player_passes([event, event])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "another-provider"),
        ("external_match_id", "another-match"),
    ],
)
def test_rejects_mixed_sources_or_matches(field: str, value: str) -> None:
    first = make_pass(1)
    data = make_pass(2).model_dump()
    data[field] = value
    second = MatchPass.model_validate(data)

    with pytest.raises(ValueError, match="one provider and match"):
        aggregate_player_passes([first, second])


@pytest.mark.parametrize(
    "field",
    ["external_team_id", "external_player_id"],
)
def test_rejects_missing_references(field: str) -> None:
    data = make_pass(1).model_dump()
    data[field] = None
    event = MatchPass.model_validate(data)

    with pytest.raises(ValueError, match="team and player references"):
        aggregate_player_passes([event])


def test_rejects_player_assigned_to_two_teams() -> None:
    passes = [
        make_pass(1),
        make_pass(2, team_id="team-2"),
    ]

    with pytest.raises(ValueError, match="multiple teams"):
        aggregate_player_passes(passes)


def test_rejects_unsupported_period() -> None:
    with pytest.raises(ValueError, match="Unsupported pass period"):
        aggregate_player_passes([make_pass(1, period=6)])
