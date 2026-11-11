from decimal import Decimal

import pytest

from football_analytics.ingestion.team_pass_statistics import (
    aggregate_team_passes,
)
from football_analytics.integrations.base import MatchPass


def make_pass(
    index: int,
    *,
    outcome: str = "complete",
    player_id: str = "player-1",
    team_id: str = "team-1",
    period: int = 1,
    match_id: str = "3869685",
) -> MatchPass:
    return MatchPass.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": match_id,
            "external_event_id": f"pass-{index}",
            "source_index": index,
            "period": period,
            "timestamp_ms": index * 1000,
            "external_team_id": team_id,
            "external_player_id": player_id,
            "outcome": outcome,
        }
    )


def test_calculates_team_percentage_from_totals() -> None:
    passes = [make_pass(index) for index in range(1, 10)]
    passes += [
        make_pass(10, outcome="incomplete"),
        make_pass(11, player_id="player-2", outcome="unknown"),
    ]

    team = aggregate_team_passes(passes)[0]

    assert team.attempted == 11
    assert team.completed == 9
    assert team.not_completed == 1
    assert team.unknown == 1
    assert team.completion_pct == Decimal("81.82")


def test_preserves_categories_identity_and_ordered_sources() -> None:
    passes = [
        make_pass(index, outcome=outcome)
        for index, outcome in enumerate(
            ["complete", "incomplete", "out", "offside", "unknown"],
            start=1,
        )
    ]

    team = aggregate_team_passes(list(reversed(passes)))[0]

    assert team.provider == "statsbomb"
    assert team.external_match_id == "3869685"
    assert team.external_team_id == "team-1"
    assert team.completed == 1
    assert team.incomplete == 1
    assert team.out == 1
    assert team.offside == 1
    assert team.unknown == 1
    assert team.not_completed == 3
    assert team.attempted == 5
    assert team.completion_pct == Decimal("20.00")
    assert team.external_event_ids == tuple(f"pass-{index}" for index in range(1, 6))


def test_separates_teams() -> None:
    result = aggregate_team_passes(
        [
            make_pass(1),
            make_pass(2, player_id="player-2", team_id="team-2", outcome="out"),
        ]
    )

    by_team = {team.external_team_id: team for team in result}

    assert len(result) == 2
    assert by_team["team-1"].completed == 1
    assert by_team["team-2"].completed == 0
    assert by_team["team-2"].not_completed == 1
    assert by_team["team-2"].external_event_ids == ("pass-2",)


def test_includes_extra_time_and_excludes_shootout() -> None:
    team = aggregate_team_passes(
        [
            make_pass(1),
            make_pass(2, period=3),
            make_pass(3, period=4),
            make_pass(4, period=5),
        ]
    )[0]

    assert team.attempted == 3
    assert team.external_event_ids == ("pass-1", "pass-2", "pass-3")


def test_rejects_duplicate_events() -> None:
    event = make_pass(1)

    with pytest.raises(ValueError, match="Duplicate pass event"):
        aggregate_team_passes([event, event])


def test_rejects_mixed_matches() -> None:
    with pytest.raises(ValueError, match="one provider and match"):
        aggregate_team_passes(
            [
                make_pass(1),
                make_pass(2, match_id="another-match"),
            ]
        )


@pytest.mark.parametrize("shootout_only", [False, True])
def test_returns_no_observed_teams_without_playing_passes(
    shootout_only: bool,
) -> None:
    passes = [make_pass(1, period=5)] if shootout_only else []

    assert aggregate_team_passes(passes) == []
