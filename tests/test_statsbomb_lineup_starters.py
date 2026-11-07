import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.lineups import normalize_lineups


def lineup_payload() -> list[dict[str, object]]:
    return [
        {
            "team_id": 771,
            "lineup": [{"player_id": player_id} for player_id in range(1, 13)],
        },
        {
            "team_id": 779,
            "lineup": [{"player_id": player_id} for player_id in range(101, 113)],
        },
    ]


def starting_players() -> dict[str, set[str]]:
    return {
        "771": {str(player_id) for player_id in range(1, 12)},
        "779": {str(player_id) for player_id in range(101, 112)},
    }


def test_classifies_starters_and_nonstarters() -> None:
    items = normalize_lineups(
        lineup_payload(),
        match_id=3869685,
        starting_xi=starting_players(),
    )

    assert len(items) == 24
    assert sum(item.is_starter is True for item in items) == 22
    assert sum(item.is_starter is False for item in items) == 2
    assert all(item.is_starter is not None for item in items)

    by_player = {item.external_player_id: item.is_starter for item in items}

    assert by_player["1"] is True
    assert by_player["12"] is False
    assert by_player["101"] is True
    assert by_player["112"] is False


def test_without_events_keeps_starter_status_unknown() -> None:
    items = normalize_lineups(
        lineup_payload(),
        match_id=3869685,
    )

    assert all(item.is_starter is None for item in items)


def test_wrong_teams_are_rejected() -> None:
    starters = starting_players()
    starters["780"] = starters.pop("779")

    with pytest.raises(ProviderUnavailable, match="teams differ"):
        normalize_lineups(
            lineup_payload(),
            match_id=3869685,
            starting_xi=starters,
        )


def test_unlisted_starter_is_rejected() -> None:
    starters = starting_players()
    starters["771"].remove("11")
    starters["771"].add("999")

    with pytest.raises(ProviderUnavailable, match="unlisted players"):
        normalize_lineups(
            lineup_payload(),
            match_id=3869685,
            starting_xi=starters,
        )


def test_incorrect_starter_count_is_rejected() -> None:
    starters = starting_players()
    starters["771"].remove("11")

    with pytest.raises(ProviderUnavailable, match="11 distinct"):
        normalize_lineups(
            lineup_payload(),
            match_id=3869685,
            starting_xi=starters,
        )


def test_missing_team_is_rejected() -> None:
    starters = starting_players()
    del starters["779"]

    with pytest.raises(ProviderUnavailable, match="exactly two teams"):
        normalize_lineups(
            lineup_payload(),
            match_id=3869685,
            starting_xi=starters,
        )
