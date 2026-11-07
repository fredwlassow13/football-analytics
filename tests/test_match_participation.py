import pytest

from football_analytics.ingestion.match_participation import (
    reconstruct_match_participation,
)
from football_analytics.integrations.base import ProviderUnavailable

Payloads = tuple[
    list[dict[str, object]],
    list[dict[str, object]],
]


def event(
    name: str,
    index: int,
    period: int,
    timestamp: str,
    team_id: int = 771,
    **details: object,
) -> dict[str, object]:
    return {
        "type": {"name": name},
        "index": index,
        "period": period,
        "timestamp": timestamp,
        "team": {"id": team_id},
        **details,
    }


@pytest.fixture
def match_payloads() -> Payloads:
    lineups = [
        {
            "team_id": team_id,
            "lineup": [{"player_id": player_id} for player_id in range(first_id, first_id + 12)],
        }
        for team_id, first_id in [(771, 1), (779, 101)]
    ]

    events = [
        event(
            "Starting XI",
            index,
            1,
            "00:00:00.000",
            team_id=team_id,
            tactics={
                "lineup": [
                    {"player": {"id": player_id}} for player_id in range(first_id, first_id + 11)
                ]
            },
        )
        for index, team_id, first_id in [
            (1, 771, 1),
            (2, 779, 101),
        ]
    ]

    events.extend(
        [
            event(
                "Player Off",
                3,
                1,
                "00:00:10.000",
                player={"id": 1},
            ),
            event(
                "Player On",
                4,
                1,
                "00:00:15.000",
                player={"id": 1},
            ),
            event("Half End", 10, 1, "00:01:00.000"),
            event(
                "Substitution",
                20,
                2,
                "00:00:30.000",
                player={"id": 2},
                substitution={"replacement": {"id": 12}},
            ),
            event("Half End", 30, 2, "00:01:00.000"),
        ]
    )

    return lineups, events


def test_reconstructs_participation(
    match_payloads: Payloads,
) -> None:
    lineups, events = match_payloads

    result = reconstruct_match_participation(lineups, events, match_id=1)

    by_player = {player.external_player_id: player for player in result}

    assert len(result) == 24
    assert sum(bool(player.intervals) for player in result) == 23

    assert by_player["1"].elapsed_on_field_ms == 115_000
    assert [(interval.start_ms, interval.end_ms) for interval in by_player["1"].intervals] == [
        (0, 10_000),
        (15_000, 120_000),
    ]

    assert by_player["2"].elapsed_on_field_ms == 90_000
    assert by_player["12"].elapsed_on_field_ms == 30_000
    assert by_player["101"].elapsed_on_field_ms == 120_000

    assert by_player["112"].intervals == []
    assert by_player["112"].elapsed_on_field_ms is None


def test_rejects_wrong_team(
    match_payloads: Payloads,
) -> None:
    lineups, events = match_payloads
    events[2]["team"] = {"id": 779}

    with pytest.raises(ValueError, match="wrong team"):
        reconstruct_match_participation(lineups, events, 1)


def test_rejects_unlisted_player(
    match_payloads: Payloads,
) -> None:
    lineups, events = match_payloads
    events[2]["player"] = {"id": 999}

    with pytest.raises(ValueError, match="unlisted player"):
        reconstruct_match_participation(lineups, events, 1)


def test_rejects_return_after_substitution(
    match_payloads: Payloads,
) -> None:
    lineups, events = match_payloads
    events.append(
        event(
            "Player On",
            21,
            2,
            "00:00:40.000",
            player={"id": 2},
        )
    )

    with pytest.raises(ValueError, match="terminal exit"):
        reconstruct_match_participation(lineups, events, 1)


def test_rejects_event_after_period_end(
    match_payloads: Payloads,
) -> None:
    lineups, events = match_payloads
    events[2]["timestamp"] = "00:02:00.000"

    with pytest.raises(ValueError, match="exceeds"):
        reconstruct_match_participation(lineups, events, 1)


def test_rejects_missing_period_end(
    match_payloads: Payloads,
) -> None:
    lineups, events = match_payloads
    events.pop()

    with pytest.raises(ProviderUnavailable):
        reconstruct_match_participation(lineups, events, 1)


def test_rejects_exit_without_entry(
    match_payloads: Payloads,
) -> None:
    lineups, events = match_payloads
    events.append(
        event(
            "Player Off",
            21,
            2,
            "00:00:40.000",
            team_id=779,
            player={"id": 112},
        )
    )

    with pytest.raises(ValueError, match="not active"):
        reconstruct_match_participation(lineups, events, 1)


def test_bench_yellow_card_does_not_create_participation(
    match_payloads: Payloads,
) -> None:
    lineups, events = match_payloads
    events.append(
        event(
            "Bad Behaviour",
            21,
            2,
            "00:00:40.000",
            team_id=779,
            player={"id": 112},
            bad_behaviour={"card": {"name": "Yellow Card"}},
        )
    )

    result = reconstruct_match_participation(lineups, events, 1)

    player = next(item for item in result if item.external_player_id == "112")

    assert player.intervals == []
    assert player.elapsed_on_field_ms is None


def test_red_card_ends_active_player_interval(
    match_payloads: Payloads,
) -> None:
    lineups, events = match_payloads
    events.append(
        event(
            "Foul Committed",
            21,
            2,
            "00:00:40.000",
            player={"id": 3},
            foul_committed={"card": {"name": "Red Card"}},
        )
    )

    result = reconstruct_match_participation(lineups, events, 1)

    player = next(item for item in result if item.external_player_id == "3")

    assert player.elapsed_on_field_ms == 100_000
