import pytest

from football_analytics.ingestion.match_participation import (
    PlayerParticipation,
)
from football_analytics.ingestion.participation import (
    ParticipationInterval,
)
from football_analytics.ingestion.team_occupancy import (
    build_team_occupancy,
)


def player(
    team_id: str,
    player_id: str,
    intervals: list[tuple[int, int]],
) -> PlayerParticipation:
    return PlayerParticipation(
        external_match_id="1",
        external_team_id=team_id,
        external_player_id=player_id,
        is_starter=None,
        intervals=[ParticipationInterval(start_ms=start, end_ms=end) for start, end in intervals],
        elapsed_on_field_ms=(sum(end - start for start, end in intervals) if intervals else None),
    )


@pytest.fixture
def players() -> list[PlayerParticipation]:
    return [
        player(team_id, f"{team_id}-{number}", [(0, 100_000)])
        for team_id in ("A", "B")
        for number in range(11)
    ]


def test_full_teams_remain_at_eleven(
    players: list[PlayerParticipation],
) -> None:
    result = build_team_occupancy(players, 100_000)

    assert [
        (
            item.external_team_id,
            item.start_ms,
            item.end_ms,
            item.players_on_field,
        )
        for item in result
    ] == [
        ("A", 0, 100_000, 11),
        ("B", 0, 100_000, 11),
    ]


def test_simultaneous_substitution_does_not_create_twelfth_player(
    players: list[PlayerParticipation],
) -> None:
    players[0] = player("A", "A-0", [(0, 30_000)])
    players.append(player("A", "A-reserve", [(30_000, 100_000)]))

    result = build_team_occupancy(players, 100_000)
    team_a = [item for item in result if item.external_team_id == "A"]

    assert len(team_a) == 1
    assert team_a[0].start_ms == 0
    assert team_a[0].end_ms == 100_000
    assert team_a[0].players_on_field == 11


def test_temporary_exit_reduces_count_until_return(
    players: list[PlayerParticipation],
) -> None:
    players[0] = player(
        "A",
        "A-0",
        [(0, 10_000), (15_000, 100_000)],
    )

    result = build_team_occupancy(players, 100_000)
    team_a = [item for item in result if item.external_team_id == "A"]

    assert [(item.start_ms, item.end_ms, item.players_on_field) for item in team_a] == [
        (0, 10_000, 11),
        (10_000, 15_000, 10),
        (15_000, 100_000, 11),
    ]

    # Soma dos tempos individuais = integral da contagem coletiva.
    collective_total = sum((item.end_ms - item.start_ms) * item.players_on_field for item in result)

    individual_total = sum(interval.duration_ms for item in players for interval in item.intervals)

    assert collective_total == individual_total == 2_195_000


def test_more_than_eleven_players_is_rejected(
    players: list[PlayerParticipation],
) -> None:
    players.append(player("A", "A-extra", [(0, 100_000)]))

    with pytest.raises(ValueError, match="Invalid player count"):
        build_team_occupancy(players, 100_000)


def test_overlapping_intervals_are_rejected(
    players: list[PlayerParticipation],
) -> None:
    players[0] = player(
        "A",
        "A-0",
        [(0, 60_000), (50_000, 100_000)],
    )

    with pytest.raises(ValueError, match="Overlapping"):
        build_team_occupancy(players, 100_000)


@pytest.mark.parametrize(
    "interval",
    [
        (-1, 100_000),
        (20_000, 10_000),
        (0, 100_001),
    ],
)
def test_invalid_interval_bounds_are_rejected(
    players: list[PlayerParticipation],
    interval: tuple[int, int],
) -> None:
    players[0] = player("A", "A-0", [interval])

    with pytest.raises(ValueError, match="Invalid participation interval"):
        build_team_occupancy(players, 100_000)
