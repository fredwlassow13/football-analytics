from decimal import Decimal

import pytest

from football_analytics.ingestion.shot_xg_statistics import aggregate_player_shot_xg
from football_analytics.integrations.base import MatchShot


def make_shot(
    index: int,
    *,
    xg: float | None = 0.1,
    shot_type: str | None = "Open Play",
    outcome: str = "saved",
    period: int = 1,
    player_id: str = "player-1",
    team_id: str = "team-1",
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
            "shot_type": shot_type,
            "statsbomb_xg": xg,
        }
    )


def test_separates_penalties_and_reconciles_xg_without_rounding() -> None:
    result = aggregate_player_shot_xg(
        [
            make_shot(1, xg=0.1),
            make_shot(2, xg=0.2, shot_type="Penalty", outcome="goal"),
        ]
    )

    player = result[0]
    assert player.overall.shots == 2
    assert player.overall.goals == 1
    assert player.overall.xg_total == Decimal("0.3")
    assert player.penalties.shots == 1
    assert player.penalties.xg_total == Decimal("0.2")
    assert player.non_penalties.shots == 1
    assert player.non_penalties.xg_total == Decimal("0.1")
    assert player.unclassified_type.shots == 0
    assert player.unclassified_type.xg_total is None


def test_does_not_present_partial_xg_as_complete_total() -> None:
    result = aggregate_player_shot_xg(
        [
            make_shot(1, xg=0.2),
            make_shot(2, xg=None),
        ]
    )

    overall = result[0].overall
    assert overall.shots == 2
    assert overall.shots_with_xg == 1
    assert overall.missing_xg == 1
    assert overall.available_xg_sum == Decimal("0.2")
    assert overall.xg_total is None


def test_preserves_complete_absence_of_xg() -> None:
    result = aggregate_player_shot_xg([make_shot(1, xg=None)])

    overall = result[0].overall
    assert overall.available_xg_sum is None
    assert overall.xg_total is None
    assert overall.missing_xg == 1


def test_preserves_observed_zero_xg() -> None:
    result = aggregate_player_shot_xg([make_shot(1, xg=0.0)])

    overall = result[0].overall
    assert overall.available_xg_sum == Decimal("0")
    assert overall.xg_total == Decimal("0")
    assert overall.missing_xg == 0


def test_does_not_assume_unknown_types_are_non_penalties() -> None:
    result = aggregate_player_shot_xg(
        [
            make_shot(1, shot_type=None),
            make_shot(2, shot_type="New Source Category"),
        ]
    )

    player = result[0]
    assert player.non_penalties.shots == 0
    assert player.penalties.shots == 0
    assert player.unclassified_type.shots == 2
    assert player.unclassified_type.xg_total == Decimal("0.2")


def test_includes_extra_time_and_excludes_shootout() -> None:
    result = aggregate_player_shot_xg(
        [
            make_shot(1, period=3),
            make_shot(2, period=4),
            make_shot(3, period=5, xg=0.8, shot_type="Penalty"),
        ]
    )

    player = result[0]
    assert player.overall.shots == 2
    assert player.overall.xg_total == Decimal("0.2")
    assert player.penalties.shots == 0


def test_separates_players_and_teams() -> None:
    result = aggregate_player_shot_xg(
        [
            make_shot(1, xg=0.1),
            make_shot(2, xg=0.2, player_id="player-2"),
            make_shot(3, xg=0.3, player_id="player-3", team_id="team-2"),
        ]
    )

    by_player = {player.external_player_id: player for player in result}

    assert len(result) == 3
    assert by_player["player-1"].overall.xg_total == Decimal("0.1")
    assert by_player["player-2"].overall.xg_total == Decimal("0.2")
    assert by_player["player-3"].external_team_id == "team-2"
    assert by_player["player-3"].overall.xg_total == Decimal("0.3")


def test_reuses_duplicate_event_validation() -> None:
    shot = make_shot(1)

    with pytest.raises(ValueError, match="Duplicate shot event"):
        aggregate_player_shot_xg([shot, shot])


def test_accepts_empty_input() -> None:
    assert aggregate_player_shot_xg([]) == []
