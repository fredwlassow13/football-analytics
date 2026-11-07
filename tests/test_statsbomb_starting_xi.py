import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.starting_xi import (
    normalize_starting_xi,
)


def starting_event(
    team_id: int,
    player_ids: list[int] | None = None,
) -> dict[str, object]:
    if player_ids is None:
        player_ids = list(range(team_id * 100, team_id * 100 + 11))

    return {
        "type": {"name": "Starting XI"},
        "team": {"id": team_id},
        "tactics": {"lineup": [{"player": {"id": player_id}} for player_id in player_ids]},
    }


def test_normalizes_two_starting_lineups() -> None:
    payload = [
        starting_event(771),
        starting_event(779),
        {"type": {"name": "Half Start"}},
    ]

    result = normalize_starting_xi(payload)

    assert set(result) == {"771", "779"}
    assert result["771"] == {str(player_id) for player_id in range(77100, 77111)}
    assert result["779"] == {str(player_id) for player_id in range(77900, 77911)}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        [starting_event(771)],
        [starting_event(771), starting_event(771)],
    ],
)
def test_invalid_or_incomplete_events_are_rejected(
    payload: object,
) -> None:
    with pytest.raises(ProviderUnavailable):
        normalize_starting_xi(payload)


@pytest.mark.parametrize("player_count", [10, 12])
def test_requires_eleven_players(player_count: int) -> None:
    payload = [
        starting_event(
            771,
            player_ids=list(range(1, player_count + 1)),
        ),
        starting_event(779),
    ]

    with pytest.raises(ProviderUnavailable):
        normalize_starting_xi(payload)


def test_duplicate_player_is_rejected() -> None:
    payload = [
        starting_event(
            771,
            player_ids=list(range(1, 11)) + [1],
        ),
        starting_event(779),
    ]

    with pytest.raises(ProviderUnavailable, match="distinct"):
        normalize_starting_xi(payload)


def test_missing_team_is_rejected() -> None:
    event = starting_event(771)
    event["team"] = None

    with pytest.raises(ProviderUnavailable, match="no team"):
        normalize_starting_xi([event, starting_event(779)])


@pytest.mark.parametrize("invalid_id", [0, -1])
def test_invalid_player_id_is_rejected(invalid_id: int) -> None:
    payload = [
        starting_event(
            771,
            player_ids=list(range(1, 11)) + [invalid_id],
        ),
        starting_event(779),
    ]

    with pytest.raises(ProviderUnavailable):
        normalize_starting_xi(payload)
