import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.lineups import normalize_lineups


def lineup(
    *players: dict[str, object],
    team_id: int = 771,
) -> dict[str, object]:
    return {
        "team_id": team_id,
        "lineup": list(players),
    }


def test_normalizes_lineup() -> None:
    payload = [lineup({"player_id": 10481, "jersey_number": 8})]

    items = normalize_lineups(payload, match_id=3869685)

    assert len(items) == 1

    item = items[0]

    assert item.provider == "statsbomb"
    assert item.external_match_id == "3869685"
    assert item.external_team_id == "771"
    assert item.external_player_id == "10481"
    assert item.shirt_number == 8
    assert item.is_starter is None


def test_missing_shirt_number_remains_missing() -> None:
    items = normalize_lineups(
        [lineup({"player_id": 10481})],
        match_id=3869685,
    )

    assert items[0].shirt_number is None


def test_identical_entries_are_deduplicated() -> None:
    player = {"player_id": 10481, "jersey_number": 8}

    items = normalize_lineups(
        [lineup(player, player)],
        match_id=3869685,
    )

    assert len(items) == 1


def test_player_cannot_belong_to_two_teams_in_same_match() -> None:
    player = {"player_id": 10481, "jersey_number": 8}

    payload = [
        lineup(player, team_id=771),
        lineup(player, team_id=779),
    ]

    with pytest.raises(ProviderUnavailable, match="Conflicting"):
        normalize_lineups(payload, match_id=3869685)


def test_conflicting_shirt_numbers_are_rejected() -> None:
    payload = [
        lineup(
            {"player_id": 10481, "jersey_number": 8},
            {"player_id": 10481, "jersey_number": 9},
        )
    ]

    with pytest.raises(ProviderUnavailable, match="Conflicting"):
        normalize_lineups(payload, match_id=3869685)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [lineup({"player_id": 0})],
        [lineup({"player_id": "10481"})],
        [lineup({"player_id": 10481, "jersey_number": -1})],
        [lineup({"player_id": 10481}, team_id=0)],
    ],
)
def test_invalid_data_is_rejected(payload: object) -> None:
    with pytest.raises(
        ProviderUnavailable,
        match="invalid lineup data",
    ):
        normalize_lineups(payload, match_id=3869685)


@pytest.mark.parametrize("match_id", [0, -1])
def test_invalid_match_id_is_rejected(match_id: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        normalize_lineups([], match_id=match_id)


def test_empty_payload_returns_no_lineups() -> None:
    assert normalize_lineups([], match_id=3869685) == []
