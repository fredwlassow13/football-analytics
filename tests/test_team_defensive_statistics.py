import pytest

from football_analytics.ingestion.defensive_statistics import (
    aggregate_player_defensive_actions,
)
from football_analytics.ingestion.team_defensive_statistics import (
    aggregate_team_defensive_actions,
)
from football_analytics.integrations.base import MatchBlock


def block(
    index: int,
    *,
    player_id: str = "player-1",
    team_id: str = "779",
    period: int = 1,
    offensive: bool | None = None,
) -> MatchBlock:
    return MatchBlock(
        provider="statsbomb",
        external_match_id="3869685",
        external_event_id=f"event-{index}",
        source_index=index,
        period=period,
        timestamp_ms=index * 1000,
        external_team_id=team_id,
        external_player_id=player_id,
        offensive=offensive,
    )


def test_combines_players_and_preserves_flag_categories() -> None:
    team = aggregate_team_defensive_actions(
        [
            block(3, player_id="player-2"),
            block(1, offensive=True),
            block(2, player_id="player-2", offensive=False),
        ],
        available_periods=(1, 2),
    )[0]

    assert team.provider == "statsbomb"
    assert team.external_match_id == "3869685"
    assert team.external_team_id == "779"
    assert team.periods == (1, 2)
    assert team.event_count == 3
    assert team.external_event_ids == ("event-1", "event-2", "event-3")

    action = team.actions[0]
    assert action.event_type == "Block"
    assert action.event_count == 3
    assert action.flags["offensive"].model_dump() == {
        "true": 1,
        "false": 1,
        "unknown": 1,
    }


def test_separates_teams_and_reconciles_player_totals() -> None:
    events = [
        block(1),
        block(2, player_id="player-2"),
        block(3, player_id="player-3", team_id="771"),
        block(4, player_id="player-3", team_id="771"),
        block(5, player_id="player-4", team_id="771"),
    ]

    players = aggregate_player_defensive_actions(events, available_periods=(1, 2))
    teams = aggregate_team_defensive_actions(events, available_periods=(1, 2))

    assert len(teams) == 2
    assert sum(team.event_count for team in teams) == 5

    for team in teams:
        team_players = [
            player for player in players if player.external_team_id == team.external_team_id
        ]
        assert team.event_count == sum(player.event_count for player in team_players)
        assert set(team.external_event_ids) == {
            event_id for player in team_players for event_id in player.external_event_ids
        }
        assert sum(action.event_count for action in team.actions) == team.event_count


def test_reconciles_individual_flags_with_team_flags() -> None:
    events = [
        block(1, offensive=True),
        block(2, offensive=True),
        block(3, player_id="player-2", offensive=False),
        block(4, player_id="player-2"),
    ]

    players = aggregate_player_defensive_actions(events, available_periods=(1, 2))
    team = aggregate_team_defensive_actions(events, available_periods=(1, 2))[0]

    collective = team.actions[0].flags["offensive"]

    for category in ("true", "false", "unknown"):
        assert getattr(collective, category) == sum(
            getattr(player.actions[0].flags["offensive"], category) for player in players
        )


def test_selects_extra_time_and_excludes_shootout() -> None:
    team = aggregate_team_defensive_actions(
        [
            block(1, period=1),
            block(2, period=3),
            block(3, period=4),
            block(4, period=5),
        ],
        available_periods=(1, 2, 3, 4),
        periods=(3, 4),
    )[0]

    assert team.periods == (3, 4)
    assert team.event_count == 2
    assert team.external_event_ids == ("event-2", "event-3")


def test_reuses_duplicate_validation() -> None:
    event = block(1)

    with pytest.raises(ValueError, match="Duplicate"):
        aggregate_team_defensive_actions([event, event], available_periods=(1, 2))


def test_reuses_player_team_validation() -> None:
    with pytest.raises(ValueError, match="multiple teams"):
        aggregate_team_defensive_actions(
            [block(1), block(2, team_id="771")],
            available_periods=(1, 2),
        )


def test_rejects_unavailable_periods() -> None:
    with pytest.raises(ValueError, match="Requested periods"):
        aggregate_team_defensive_actions([], available_periods=(1, 2), periods=(3,))


def test_returns_no_teams_without_observed_events() -> None:
    assert aggregate_team_defensive_actions([], available_periods=(1, 2)) == []

    assert (
        aggregate_team_defensive_actions(
            [block(1, period=2)],
            available_periods=(1, 2),
            periods=(1,),
        )
        == []
    )
