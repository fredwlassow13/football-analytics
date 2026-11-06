import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.teams import normalize_teams


def make_match(
    home_id: int,
    home_name: str,
    away_id: int,
    away_name: str,
) -> dict[str, object]:
    return {
        "home_team": {
            "home_team_id": home_id,
            "home_team_name": home_name,
        },
        "away_team": {
            "away_team_id": away_id,
            "away_team_name": away_name,
        },
    }


def test_repeated_teams_are_not_duplicated() -> None:
    payload = [
        make_match(781, "Brazil", 779, "Argentina"),
        make_match(779, "Argentina", 781, "Brazil"),
    ]

    teams = normalize_teams(payload)

    assert len(teams) == 2
    assert [team.name for team in teams] == ["Argentina", "Brazil"]
    assert teams[0].external_team_id == "779"
    assert teams[1].external_team_id == "781"
    assert all(team.provider == "statsbomb" for team in teams)


def test_conflicting_names_are_rejected() -> None:
    payload = [
        make_match(781, "Brazil", 779, "Argentina"),
        make_match(781, "Brasil", 779, "Argentina"),
    ]

    with pytest.raises(ProviderUnavailable, match="Conflicting names"):
        normalize_teams(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [{"home_team": {}}],
        [make_match(0, "Brazil", 779, "Argentina")],
        [make_match(781, "", 779, "Argentina")],
    ],
)
def test_invalid_team_data_is_rejected(payload: object) -> None:
    with pytest.raises(ProviderUnavailable, match="invalid team data"):
        normalize_teams(payload)


def test_empty_list_returns_no_teams() -> None:
    assert normalize_teams([]) == []


def test_blank_names_are_rejected() -> None:
    with pytest.raises(ProviderUnavailable, match="invalid team data"):
        normalize_teams([make_match(1, "   ", 2, "Other")])


def test_same_team_cannot_face_itself() -> None:
    with pytest.raises(ProviderUnavailable, match="Same home and away"):
        normalize_teams([make_match(1, "Team", 1, "Team")])
