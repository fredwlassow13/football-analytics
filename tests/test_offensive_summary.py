from decimal import Decimal

import pytest

from football_analytics.ingestion.offensive_summary import build_offensive_summary
from football_analytics.integrations.base import MatchDribble, MatchPass, MatchShot


def make_event(
    index: int,
    *,
    event_type: str = "Pass",
    period: int = 1,
    player_id: str = "player-1",
    team_id: str = "team-1",
    xg: float | None = 0.2,
):
    header = {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": f"{event_type}-{index}",
        "source_index": index,
        "period": period,
        "timestamp_ms": index * 1000,
        "external_team_id": team_id,
        "external_player_id": player_id,
    }
    if event_type == "Shot":
        return MatchShot.model_validate(
            header
            | {
                "outcome": "goal",
                "shot_type": "Open Play",
                "statsbomb_xg": xg,
            }
        )
    if event_type == "Dribble":
        return MatchDribble.model_validate(header | {"outcome": "complete"})
    return MatchPass.model_validate(header | {"outcome": "complete"})


def build(events, *, periods=None):
    return build_offensive_summary(
        events,
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
        available_periods=(1, 2, 3, 4),
        periods=periods,
    )


def test_combines_metrics_and_reconciles_players_with_team() -> None:
    result = build(
        [
            make_event(3, event_type="Dribble"),
            make_event(1),
            make_event(2, event_type="Shot", player_id="player-2"),
        ]
    )

    assert result.population_basis == "observed_offensive_events"
    assert result.events_sha256 == "a" * 64
    assert result.external_event_ids == ("Pass-1", "Shot-2", "Dribble-3")
    assert len(result.teams) == 1
    assert len(result.players) == 2

    team = result.teams[0]
    players = {item.external_player_id: item for item in result.players}

    assert team.passes is not None
    assert team.shots is not None
    assert team.dribbles is not None
    assert team.xg is not None
    assert team.passes.attempted == 1
    assert team.shots.shots == 1
    assert team.shots.shots_on_target == 1
    assert team.dribbles.completed == 1
    assert team.xg.overall.xg_total == Decimal("0.2")

    assert players["player-1"].shots is None
    assert players["player-2"].passes is None
    assert (
        sum(player.passes.attempted for player in result.players if player.passes is not None)
        == team.passes.attempted
    )
    assert (
        sum(player.shots.shots for player in result.players if player.shots is not None)
        == team.shots.shots
    )
    assert (
        sum(player.dribbles.attempted for player in result.players if player.dribbles is not None)
        == team.dribbles.attempted
    )


def test_applies_same_period_scope_to_every_metric() -> None:
    result = build(
        [
            make_event(1),
            make_event(2, event_type="Shot", period=2),
            make_event(3, event_type="Dribble", period=3),
            make_event(4, event_type="Shot", period=5),
        ],
        periods=(1, 2),
    )

    assert result.periods == (1, 2)
    assert result.external_event_ids == ("Pass-1", "Shot-2")
    assert result.teams[0].dribbles is None
    assert result.teams[0].shots.shots == 1


def test_preserves_missing_xg_and_separates_teams() -> None:
    result = build(
        [
            make_event(1, event_type="Shot"),
            make_event(
                2,
                event_type="Shot",
                team_id="team-2",
                player_id="player-2",
                xg=None,
            ),
        ]
    )

    teams = {item.external_team_id: item for item in result.teams}
    assert teams["team-1"].xg.overall.xg_total == Decimal("0.2")
    assert teams["team-2"].xg.overall.shots == 1
    assert teams["team-2"].xg.overall.missing_xg == 1
    assert teams["team-2"].xg.overall.xg_total is None


def test_rejects_player_changing_team_across_event_types() -> None:
    with pytest.raises(ValueError, match="multiple teams"):
        build(
            [
                make_event(1),
                make_event(2, event_type="Shot", team_id="team-2"),
            ]
        )


def test_rejects_shared_source_index_across_event_types() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        build([make_event(1), make_event(1, event_type="Shot")])


def test_rejects_events_from_another_requested_match() -> None:
    event = make_event(1)
    event.external_match_id = "another-match"

    with pytest.raises(ValueError, match="requested provider and match"):
        build([event])


def test_empty_scope_does_not_invent_players_or_teams() -> None:
    result = build([make_event(1)], periods=(2,))

    assert result.periods == (2,)
    assert result.players == ()
    assert result.teams == ()
    assert result.external_event_ids == ()
