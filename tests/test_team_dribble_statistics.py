from decimal import Decimal

import pytest

from football_analytics.ingestion.team_dribble_statistics import (
    aggregate_team_dribbles,
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


def test_calculates_team_rate_and_preserves_ordered_sources() -> None:
    dribbles = [make_dribble(index) for index in range(1, 10)]
    dribbles += [
        make_dribble(10, outcome="incomplete"),
        make_dribble(11, player_id="player-2", outcome="incomplete"),
    ]

    team = aggregate_team_dribbles(list(reversed(dribbles)))[0]

    assert team.provider == "statsbomb"
    assert team.external_match_id == "3869685"
    assert team.external_team_id == "team-1"
    assert team.attempted == 11
    assert team.completed == 9
    assert team.incomplete == 2
    assert team.completion_pct == Decimal("81.82")
    assert team.external_event_ids == tuple(f"dribble-{index}" for index in range(1, 12))


def test_separates_teams() -> None:
    result = aggregate_team_dribbles(
        [
            make_dribble(1),
            make_dribble(
                2,
                player_id="player-2",
                team_id="team-2",
                outcome="incomplete",
            ),
        ]
    )
    by_team = {team.external_team_id: team for team in result}

    assert len(result) == 2
    assert by_team["team-1"].completion_pct == Decimal("100.00")
    assert by_team["team-2"].completion_pct == Decimal("0.00")
    assert by_team["team-2"].external_event_ids == ("dribble-2",)
    assert sum(team.attempted for team in result) == 2


def test_includes_extra_time_and_excludes_shootout() -> None:
    team = aggregate_team_dribbles(
        [
            make_dribble(1, period=3),
            make_dribble(2, period=4, outcome="incomplete"),
            make_dribble(3, period=5),
        ]
    )[0]

    assert team.attempted == 2
    assert team.completed == 1
    assert team.incomplete == 1
    assert team.external_event_ids == ("dribble-1", "dribble-2")


def test_rejects_duplicate_events() -> None:
    event = make_dribble(1)

    with pytest.raises(ValueError, match="Duplicate dribble event"):
        aggregate_team_dribbles([event, event])


@pytest.mark.parametrize("shootout_only", [False, True])
def test_returns_no_observed_teams_without_playing_dribbles(
    shootout_only: bool,
) -> None:
    dribbles = [make_dribble(1, period=5)] if shootout_only else []

    assert aggregate_team_dribbles(dribbles) == []
