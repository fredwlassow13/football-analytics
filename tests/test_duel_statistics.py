import pytest

from football_analytics.ingestion.duel_statistics import aggregate_player_duels
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


def test_preserves_each_tackle_result_and_aerial_losses() -> None:
    outcomes = [
        "lost",
        "won",
        "lost_in_play",
        "lost_out",
        "success",
        "success_in_play",
        "success_out",
    ]
    events = [make_duel(index, outcome=outcome) for index, outcome in enumerate(outcomes, start=1)]
    events.append(make_duel(8, duel_type="aerial_lost", outcome=None))

    player = aggregate_player_duels(list(reversed(events)))[0]

    assert player.provider == "statsbomb"
    assert player.external_match_id == "3869685"
    assert player.external_team_id == "team-1"
    assert player.external_player_id == "player-1"
    assert player.tackles.total == 7
    for outcome in outcomes:
        assert getattr(player.tackles, outcome) == 1
    assert player.aerial_lost == 1
    assert player.total_duel_events == 8
    assert player.external_event_ids == tuple(f"duel-{index}" for index in range(1, 9))


def test_separates_players_and_teams() -> None:
    result = aggregate_player_duels(
        [
            make_duel(1),
            make_duel(2, player_id="player-2"),
            make_duel(3, player_id="player-3", team_id="team-2"),
        ]
    )

    assert len(result) == 3
    assert sum(player.tackles.total for player in result) == 3
    assert result[-1].external_team_id == "team-2"


def test_includes_extra_time_and_excludes_shootout() -> None:
    player = aggregate_player_duels(
        [
            make_duel(1, period=3),
            make_duel(2, period=4, duel_type="aerial_lost", outcome=None),
            make_duel(3, period=5),
        ]
    )[0]

    assert player.tackles.total == 1
    assert player.aerial_lost == 1
    assert player.external_event_ids == ("duel-1", "duel-2")


def test_rejects_duplicate_events() -> None:
    event = make_duel(1)
    with pytest.raises(ValueError, match="Duplicate"):
        aggregate_player_duels([event, event])


def test_rejects_duplicate_source_indexes() -> None:
    first = make_duel(1)
    second = make_duel(2)
    second.source_index = 1

    with pytest.raises(ValueError, match="Duplicate"):
        aggregate_player_duels([first, second])


def test_rejects_player_assigned_to_two_teams() -> None:
    with pytest.raises(ValueError, match="multiple teams"):
        aggregate_player_duels(
            [
                make_duel(1),
                make_duel(2, team_id="team-2"),
            ]
        )


def test_requires_review_for_contradictory_aerial_outcome() -> None:
    event = make_duel(1, duel_type="aerial_lost", outcome="won")

    with pytest.raises(ValueError, match="requires review"):
        aggregate_player_duels([event])


@pytest.mark.parametrize("shootout_only", [False, True])
def test_accepts_no_playing_duels(shootout_only: bool) -> None:
    events = [make_duel(1, period=5)] if shootout_only else []
    assert aggregate_player_duels(events) == []
