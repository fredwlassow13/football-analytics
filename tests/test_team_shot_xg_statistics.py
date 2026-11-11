from decimal import Decimal

import pytest

from football_analytics.ingestion.team_shot_xg_statistics import (
    aggregate_team_shot_xg,
)
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


def test_combines_players_and_separates_penalties_without_rounding() -> None:
    team = aggregate_team_shot_xg(
        [
            make_shot(
                2,
                xg=0.200000002,
                player_id="player-2",
                shot_type="Penalty",
                outcome="goal",
            ),
            make_shot(1, xg=0.100000001),
        ]
    )[0]

    assert team.provider == "statsbomb"
    assert team.external_match_id == "3869685"
    assert team.external_team_id == "team-1"
    assert team.overall.shots == 2
    assert team.overall.goals == 1
    assert team.overall.xg_total == Decimal("0.300000003")
    assert team.penalties.xg_total == Decimal("0.200000002")
    assert team.non_penalties.xg_total == Decimal("0.100000001")
    assert team.unclassified_type.shots == 0
    assert team.unclassified_type.xg_total is None
    assert team.external_event_ids == ("shot-1", "shot-2")


def test_preserves_incomplete_xg_coverage_across_players() -> None:
    team = aggregate_team_shot_xg(
        [
            make_shot(1, xg=0.2),
            make_shot(2, xg=None, player_id="player-2"),
        ]
    )[0]

    assert team.overall.shots == 2
    assert team.overall.shots_with_xg == 1
    assert team.overall.missing_xg == 1
    assert team.overall.available_xg_sum == Decimal("0.2")
    assert team.overall.xg_total is None


def test_preserves_complete_absence_of_xg() -> None:
    team = aggregate_team_shot_xg(
        [
            make_shot(1, xg=None),
            make_shot(2, xg=None, player_id="player-2"),
        ]
    )[0]

    assert team.overall.shots_with_xg == 0
    assert team.overall.missing_xg == 2
    assert team.overall.available_xg_sum is None
    assert team.overall.xg_total is None


def test_preserves_observed_zero_xg() -> None:
    team = aggregate_team_shot_xg([make_shot(1, xg=0.0)])[0]

    assert team.overall.shots_with_xg == 1
    assert team.overall.available_xg_sum == Decimal("0")
    assert team.overall.xg_total == Decimal("0")


def test_keeps_unknown_shot_types_separate() -> None:
    team = aggregate_team_shot_xg(
        [
            make_shot(1, shot_type=None),
            make_shot(2, shot_type="New Source Category"),
        ]
    )[0]

    assert team.unclassified_type.shots == 2
    assert team.unclassified_type.xg_total == Decimal("0.2")
    assert team.penalties.shots == 0
    assert team.non_penalties.shots == 0


def test_separates_teams() -> None:
    result = aggregate_team_shot_xg(
        [
            make_shot(1, xg=0.1),
            make_shot(2, xg=0.3, player_id="player-2", team_id="team-2"),
        ]
    )
    by_team = {team.external_team_id: team for team in result}

    assert len(result) == 2
    assert by_team["team-1"].overall.xg_total == Decimal("0.1")
    assert by_team["team-2"].overall.xg_total == Decimal("0.3")


def test_includes_extra_time_and_excludes_shootout() -> None:
    team = aggregate_team_shot_xg(
        [
            make_shot(1, period=3),
            make_shot(2, period=4),
            make_shot(3, period=5, xg=0.8, shot_type="Penalty"),
        ]
    )[0]

    assert team.overall.shots == 2
    assert team.overall.xg_total == Decimal("0.2")
    assert team.penalties.shots == 0
    assert team.external_event_ids == ("shot-1", "shot-2")


def test_rejects_duplicate_events() -> None:
    event = make_shot(1)

    with pytest.raises(ValueError, match="Duplicate shot event"):
        aggregate_team_shot_xg([event, event])


@pytest.mark.parametrize("shootout_only", [False, True])
def test_returns_no_observed_teams_without_playing_shots(
    shootout_only: bool,
) -> None:
    shots = [make_shot(1, period=5)] if shootout_only else []

    assert aggregate_team_shot_xg(shots) == []
