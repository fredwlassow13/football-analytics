import pytest

from football_analytics.ingestion.team_duel_statistics import aggregate_team_duels
from football_analytics.integrations.base import MatchDuel


def make_duel(
    index: int,
    *,
    duel_type: str = "tackle",
    outcome: str | None = "won",
    player_id: str = "player-1",
    team_id: str = "team-1",
    period: int = 1,
) -> MatchDuel:
    return MatchDuel.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_event_id": f"duel-{index}",
            "source_index": index,
            "period": period,
            "timestamp_ms": index * 1000,
            "external_team_id": team_id,
            "external_player_id": player_id,
            "duel_type": duel_type,
            "outcome": outcome,
        }
    )


def test_combines_players_preserving_every_category_and_source() -> None:
    outcomes = [
        "lost",
        "won",
        "lost_in_play",
        "lost_out",
        "success",
        "success_in_play",
        "success_out",
    ]
    events = [
        make_duel(
            index,
            outcome=outcome,
            player_id=f"player-{index}",
        )
        for index, outcome in enumerate(outcomes, start=1)
    ]
    events.append(
        make_duel(
            8,
            duel_type="aerial_lost",
            outcome=None,
            player_id="player-8",
        )
    )

    team = aggregate_team_duels(list(reversed(events)))[0]

    assert team.provider == "statsbomb"
    assert team.external_match_id == "3869685"
    assert team.external_team_id == "team-1"
    assert team.tackles.total == 7
    for outcome in outcomes:
        assert getattr(team.tackles, outcome) == 1
    assert team.aerial_lost == 1
    assert team.total_duel_events == 8
    assert team.external_event_ids == tuple(f"duel-{index}" for index in range(1, 9))


def test_separates_teams() -> None:
    result = aggregate_team_duels(
        [
            make_duel(1),
            make_duel(
                2,
                team_id="team-2",
                player_id="player-2",
                duel_type="aerial_lost",
                outcome=None,
            ),
        ]
    )
    teams = {team.external_team_id: team for team in result}

    assert len(teams) == 2
    assert teams["team-1"].tackles.won == 1
    assert teams["team-1"].aerial_lost == 0
    assert teams["team-2"].tackles.total == 0
    assert teams["team-2"].aerial_lost == 1
    assert sum(team.total_duel_events for team in result) == 2


def test_includes_extra_time_and_excludes_shootout() -> None:
    team = aggregate_team_duels(
        [
            make_duel(1, period=3),
            make_duel(2, period=4, outcome="lost_out"),
            make_duel(3, period=5),
        ]
    )[0]

    assert team.total_duel_events == 2
    assert team.tackles.won == 1
    assert team.tackles.lost_out == 1
    assert team.external_event_ids == ("duel-1", "duel-2")


def test_reuses_duplicate_validation() -> None:
    event = make_duel(1)

    with pytest.raises(ValueError, match="Duplicate"):
        aggregate_team_duels([event, event])


@pytest.mark.parametrize("shootout_only", [False, True])
def test_returns_no_observed_teams_without_playing_duels(
    shootout_only: bool,
) -> None:
    events = [make_duel(1, period=5)] if shootout_only else []
    assert aggregate_team_duels(events) == []
