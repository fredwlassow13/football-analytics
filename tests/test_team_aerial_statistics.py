from decimal import Decimal

import pytest

from football_analytics.ingestion.team_aerial_statistics import (
    TeamAerialStatistics,
    aggregate_team_aerials,
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


def test_uses_team_totals_instead_of_mean_player_percentage() -> None:
    events = [make_aerial(index) for index in range(1, 10)]
    events += [
        make_aerial(10, outcome="lost"),
        make_aerial(11, player_id="player-2", outcome="lost"),
    ]

    team = aggregate_team_aerials(list(reversed(events)))[0]

    assert team.provider == "statsbomb"
    assert team.external_match_id == "3869685"
    assert team.external_team_id == "team-1"
    assert team.won == 9
    assert team.lost == 2
    assert team.contested == 11
    assert team.win_pct == Decimal("81.82")
    assert team.external_event_ids == tuple(f"aerial-{index}" for index in range(1, 12))


def test_separates_teams() -> None:
    result = aggregate_team_aerials(
        [
            make_aerial(1),
            make_aerial(
                2,
                player_id="player-2",
                team_id="team-2",
                outcome="lost",
            ),
        ]
    )
    teams = {team.external_team_id: team for team in result}

    assert len(teams) == 2
    assert teams["team-1"].win_pct == Decimal("100.00")
    assert teams["team-2"].win_pct == Decimal("0.00")
    assert teams["team-2"].external_event_ids == ("aerial-2",)
    assert sum(team.contested for team in result) == 2


def test_includes_extra_time_and_excludes_shootout() -> None:
    team = aggregate_team_aerials(
        [
            make_aerial(1, period=3),
            make_aerial(2, period=4, outcome="lost"),
            make_aerial(3, period=5),
        ]
    )[0]

    assert team.won == 1
    assert team.lost == 1
    assert team.contested == 2
    assert team.external_event_ids == ("aerial-1", "aerial-2")


def test_reuses_duplicate_validation() -> None:
    event = make_aerial(1)

    with pytest.raises(ValueError, match="Duplicate"):
        aggregate_team_aerials([event, event])


@pytest.mark.parametrize("shootout_only", [False, True])
def test_returns_no_observed_teams_without_playing_results(
    shootout_only: bool,
) -> None:
    events = [make_aerial(1, period=5)] if shootout_only else []
    assert aggregate_team_aerials(events) == []


def test_zero_results_have_no_percentage() -> None:
    team = TeamAerialStatistics(
        provider="statsbomb",
        external_match_id="3869685",
        external_team_id="team-1",
        won=0,
        lost=0,
        external_event_ids=(),
    )

    assert team.contested == 0
    assert team.win_pct is None
