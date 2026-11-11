from decimal import Decimal

import pytest

from football_analytics.ingestion.aerial_statistics import (
    PlayerAerialStatistics,
    aggregate_player_aerials,
)
from football_analytics.integrations.base import MatchAerialDuel


def make_aerial(
    index: int,
    *,
    outcome: str = "won",
    player_id: str = "player-1",
    team_id: str = "team-1",
    period: int = 1,
) -> MatchAerialDuel:
    return MatchAerialDuel.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_event_id": f"aerial-{index}",
            "source_index": index,
            "period": period,
            "timestamp_ms": index * 1000,
            "event_type": "Pass" if outcome == "won" else "Duel",
            "external_team_id": team_id,
            "external_player_id": player_id,
            "outcome": outcome,
            "evidence": ("aerial_won_flag" if outcome == "won" else "aerial_lost_subtype"),
        }
    )


def test_counts_results_rate_and_ordered_sources() -> None:
    player = aggregate_player_aerials(
        [
            make_aerial(3, outcome="lost"),
            make_aerial(1),
            make_aerial(2),
        ]
    )[0]

    assert player.provider == "statsbomb"
    assert player.external_match_id == "3869685"
    assert player.external_team_id == "team-1"
    assert player.external_player_id == "player-1"
    assert player.won == 2
    assert player.lost == 1
    assert player.contested == 3
    assert player.win_pct == Decimal("66.67")
    assert player.external_event_ids == ("aerial-1", "aerial-2", "aerial-3")


def test_separates_players_and_teams() -> None:
    result = aggregate_player_aerials(
        [
            make_aerial(1),
            make_aerial(2, player_id="player-2", outcome="lost"),
            make_aerial(3, player_id="player-3", team_id="team-2"),
        ]
    )
    players = {player.external_player_id: player for player in result}

    assert len(players) == 3
    assert players["player-1"].win_pct == Decimal("100.00")
    assert players["player-2"].win_pct == Decimal("0.00")
    assert players["player-3"].external_team_id == "team-2"
    assert sum(player.contested for player in result) == 3


def test_includes_extra_time_and_excludes_shootout() -> None:
    player = aggregate_player_aerials(
        [
            make_aerial(1, period=3),
            make_aerial(2, period=4, outcome="lost"),
            make_aerial(3, period=5),
        ]
    )[0]

    assert player.contested == 2
    assert player.win_pct == Decimal("50.00")
    assert player.external_event_ids == ("aerial-1", "aerial-2")


@pytest.mark.parametrize("duplicate", ["id", "index"])
def test_rejects_duplicate_source_events(duplicate: str) -> None:
    first = make_aerial(1)
    second = make_aerial(2)
    if duplicate == "id":
        second.external_event_id = first.external_event_id
    else:
        second.source_index = first.source_index

    with pytest.raises(ValueError, match="Duplicate"):
        aggregate_player_aerials([first, second])


@pytest.mark.parametrize("field", ["provider", "external_match_id"])
def test_rejects_mixed_sources_or_matches(field: str) -> None:
    first = make_aerial(1)
    data = make_aerial(2).model_dump()
    data[field] = "another-value"
    second = MatchAerialDuel.model_validate(data)

    with pytest.raises(ValueError, match="one provider and match"):
        aggregate_player_aerials([first, second])


@pytest.mark.parametrize("field", ["external_team_id", "external_player_id"])
def test_requires_team_and_player_references(field: str) -> None:
    data = make_aerial(1).model_dump()
    data[field] = None

    with pytest.raises(ValueError, match="team and player references"):
        aggregate_player_aerials([MatchAerialDuel.model_validate(data)])


def test_rejects_player_assigned_to_two_teams() -> None:
    with pytest.raises(ValueError, match="multiple teams"):
        aggregate_player_aerials(
            [
                make_aerial(1),
                make_aerial(2, team_id="team-2"),
            ]
        )


def test_rejects_unsupported_period() -> None:
    with pytest.raises(ValueError, match="Unsupported aerial period"):
        aggregate_player_aerials([make_aerial(1, period=6)])


@pytest.mark.parametrize("shootout_only", [False, True])
def test_returns_no_players_without_playing_results(
    shootout_only: bool,
) -> None:
    events = [make_aerial(1, period=5)] if shootout_only else []
    assert aggregate_player_aerials(events) == []


def test_zero_results_have_no_win_percentage() -> None:
    player = PlayerAerialStatistics(
        provider="statsbomb",
        external_match_id="3869685",
        external_team_id="team-1",
        external_player_id="player-1",
        won=0,
        lost=0,
        external_event_ids=(),
    )

    assert player.contested == 0
    assert player.win_pct is None
