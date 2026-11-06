from datetime import date

import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.matches import normalize_matches


def entry(**changes: object) -> dict[str, object]:
    return {
        "match_id": 1,
        "match_date": "2022-11-20",
        "competition": {"competition_id": 43},
        "season": {"season_id": 106},
        "home_team": {"home_team_id": 10},
        "away_team": {"away_team_id": 20},
        "home_score": 0,
        "away_score": 2,
        **changes,
    }


def test_normalizes_and_deduplicates_matches() -> None:
    matches = normalize_matches([entry(), entry()])
    assert len(matches) == 1
    match = matches[0]
    assert match.provider == "statsbomb"
    assert match.external_match_id == "1"
    assert match.external_season_id == "106"
    assert match.external_competition_id == "43"
    assert match.external_home_team_id == "10"
    assert match.external_away_team_id == "20"
    assert match.match_date == date(2022, 11, 20)
    assert match.home_score == 0
    assert match.away_score == 2


def test_absent_scores_stay_missing() -> None:
    payload = entry()
    del payload["home_score"]
    payload["away_score"] = None
    match = normalize_matches([payload])[0]
    assert match.home_score is None
    assert match.away_score is None


@pytest.mark.parametrize(
    "changes",
    [
        {"match_id": 0},
        {"home_score": -1},
        {"home_score": True},
        {"match_date": "2022-02-30"},
        {"match_date": None},
        {"home_team": {"home_team_id": 20}},
        {"competition": {"competition_id": "43"}},
    ],
)
def test_rejects_invalid_matches(changes: dict[str, object]) -> None:
    with pytest.raises(ProviderUnavailable):
        normalize_matches([entry(**changes)])


def test_rejects_conflicting_versions() -> None:
    with pytest.raises(ProviderUnavailable, match="Conflicting"):
        normalize_matches([entry(), entry(away_score=3)])


def test_sorts_matches_by_date() -> None:
    matches = normalize_matches([entry(match_id=2, match_date="2022-12-01"), entry()])
    assert [match.external_match_id for match in matches] == ["1", "2"]


def test_empty_matches() -> None:
    assert normalize_matches([]) == []
