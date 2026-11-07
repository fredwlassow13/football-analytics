import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.players import normalize_players


def player(**changes: object) -> dict[str, object]:
    return {
        "player_id": 5487,
        "player_name": "Antoine Griezmann",
        "player_nickname": "Grizou",
        "country": {"name": "France"},
        **changes,
    }


def lineup(*players: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "team_id": 771,
            "lineup": list(players),
        }
    ]


def test_normalizes_player() -> None:
    players = normalize_players(lineup(player()))

    assert len(players) == 1

    result = players[0]

    assert result.provider == "statsbomb"
    assert result.external_player_id == "5487"
    assert result.name == "Antoine Griezmann"
    assert result.nickname == "Grizou"
    assert result.nationality == "France"
    assert result.birth_date is None


def test_repeated_player_is_not_duplicated() -> None:
    players = normalize_players(lineup(player(), player()))

    assert len(players) == 1


def test_missing_optional_fields_remain_missing() -> None:
    players = normalize_players(
        lineup(
            {
                "player_id": 5487,
                "player_name": "Antoine Griezmann",
            }
        )
    )

    assert players[0].nickname is None
    assert players[0].nationality is None
    assert players[0].birth_date is None


def test_conflicting_player_data_is_rejected() -> None:
    payload = lineup(
        player(),
        player(player_name="Different Name"),
    )

    with pytest.raises(ProviderUnavailable, match="Conflicting"):
        normalize_players(payload)


@pytest.mark.parametrize(
    "changes",
    [
        {"player_id": 0},
        {"player_id": "5487"},
        {"player_name": ""},
        {"player_name": "   "},
        {"country": {"name": ""}},
    ],
)
def test_invalid_player_data_is_rejected(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ProviderUnavailable, match="invalid player data"):
        normalize_players(lineup(player(**changes)))


def test_players_are_sorted_by_name() -> None:
    players = normalize_players(
        lineup(
            player(player_id=2, player_name="Zidane"),
            player(player_id=1, player_name="Henry"),
        )
    )

    assert [item.name for item in players] == ["Henry", "Zidane"]


def test_empty_list_returns_no_players() -> None:
    assert normalize_players([]) == []
