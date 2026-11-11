import pytest

from football_analytics.ingestion.pass_network import build_pass_connections
from football_analytics.integrations.base import MatchPass


def make_pass(
    index: int,
    *,
    sender_id: str = "player-a",
    recipient_id: str | None = "player-b",
    team_id: str = "team-1",
    outcome: str = "complete",
    period: int = 1,
) -> MatchPass:
    return MatchPass.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_event_id": f"pass-{index}",
            "source_index": index,
            "period": period,
            "timestamp_ms": index * 1000,
            "external_team_id": team_id,
            "external_player_id": sender_id,
            "external_recipient_player_id": recipient_id,
            "outcome": outcome,
        }
    )


def test_keeps_direction_and_ordered_source_events() -> None:
    connections = build_pass_connections(
        [
            make_pass(3),
            make_pass(2, sender_id="player-b", recipient_id="player-a"),
            make_pass(1),
        ]
    )

    by_pair = {
        (
            connection.external_sender_player_id,
            connection.external_recipient_player_id,
        ): connection
        for connection in connections
    }

    forward = by_pair[("player-a", "player-b")]
    reverse = by_pair[("player-b", "player-a")]

    assert forward.provider == "statsbomb"
    assert forward.external_match_id == "3869685"
    assert forward.external_team_id == "team-1"
    assert forward.completed_passes == 2
    assert forward.external_event_ids == ("pass-1", "pass-3")
    assert reverse.completed_passes == 1
    assert reverse.external_event_ids == ("pass-2",)
    assert sum(item.completed_passes for item in connections) == 3


@pytest.mark.parametrize("outcome", ["incomplete", "out", "offside", "unknown"])
def test_excludes_noncompleted_passes_even_with_recipient(outcome: str) -> None:
    assert (
        build_pass_connections(
            [
                make_pass(1, outcome=outcome),
            ]
        )
        == []
    )


def test_excludes_shootout_before_requiring_recipient() -> None:
    assert (
        build_pass_connections(
            [
                make_pass(1, period=5, recipient_id=None),
            ]
        )
        == []
    )


def test_rejects_completed_pass_without_recipient() -> None:
    with pytest.raises(ValueError, match="requires a recipient"):
        build_pass_connections([make_pass(1, recipient_id=None)])


def test_reuses_duplicate_event_validation() -> None:
    event = make_pass(1)

    with pytest.raises(ValueError, match="Duplicate pass event"):
        build_pass_connections([event, event])


def test_separates_teams() -> None:
    connections = build_pass_connections(
        [
            make_pass(1),
            make_pass(
                2,
                sender_id="player-c",
                recipient_id="player-d",
                team_id="team-2",
            ),
        ]
    )

    assert len(connections) == 2
    assert {item.external_team_id for item in connections} == {
        "team-1",
        "team-2",
    }
    assert sum(item.completed_passes for item in connections) == 2


def test_accepts_empty_input() -> None:
    assert build_pass_connections([]) == []
