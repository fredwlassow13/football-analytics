from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from football_analytics.ingestion.tournament_baseline import (
    COUNT_FIELDS,
    XG_FIELDS,
    TeamMatchBaseline,
    aggregate_team_baselines,
    build_match_baseline,
    build_tournament_baseline,
)


def row(mid=1, **updates):
    values = dict.fromkeys(COUNT_FIELDS, 0)
    values.update(dict.fromkeys(XG_FIELDS, Decimal(0)))
    values.update(
        match_id=mid,
        team_id="1",
        team="A",
        opponent_id="2",
        opponent="B",
        periods=(1, 2),
        events_payload_id="source",
        events_sha256="a" * 64,
        matches_payload_id="catalogue",
        matches_sha256="b" * 64,
        defensive_type_counts={},
    )
    values.update(updates)
    return TeamMatchBaseline.model_validate(values)


def test_weighted_pass_completion_and_extra_time():
    result = aggregate_team_baselines(
        [
            row(1, passes=1, passes_complete=1, xg=Decimal("0.2")),
            row(2, passes=9, passes_complete=0, xg=Decimal("0.4"), periods=(1, 2, 3, 4)),
        ]
    )[0]
    assert result.pass_completion_pct == Decimal(10)
    assert result.xg_per_match == Decimal("0.3")
    assert result.extra_time_matches == 1
    assert aggregate_team_baselines([row()])[0].pass_completion_pct is None


def test_duplicate_team_match_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        aggregate_team_baselines([row(), row()])


@pytest.mark.parametrize("ids", [[], [1, 1], [0], [True]])
def test_invalid_match_selection(ids):
    with pytest.raises(ValueError):
        build_tournament_baseline(Mock(), ids)


def source_and_score():
    payload = []
    for team in (1, 2):
        payload.append(
            {
                "id": f"p{team}",
                "index": team,
                "period": 1,
                "timestamp": "00:00:01.000",
                "type": {"name": "Pass"},
                "team": {"id": team},
                "player": {"id": team * 100},
                "pass": {},
            }
        )
    payload.append(
        {
            "id": "s1",
            "index": 3,
            "period": 1,
            "timestamp": "00:00:02.000",
            "type": {"name": "Shot"},
            "team": {"id": 1},
            "player": {"id": 100},
            "shot": {
                "outcome": {"name": "Goal"},
                "statsbomb_xg": 0.25,
                "type": {"name": "Open Play"},
            },
        }
    )
    payload.extend(
        [
            {
                "id": "h1",
                "index": 4,
                "period": 1,
                "timestamp": "00:45:00.000",
                "type": {"name": "Half End"},
            },
            {
                "id": "h2",
                "index": 5,
                "period": 2,
                "timestamp": "00:45:00.000",
                "type": {"name": "Half End"},
            },
            {
                "id": "shootout",
                "index": 6,
                "period": 5,
                "timestamp": "00:00:05.000",
                "type": {"name": "Shot"},
                "team": {"id": 2},
                "player": {"id": 200},
                "shot": {"outcome": {"name": "Goal"}, "statsbomb_xg": 0.8},
            },
        ]
    )
    score = {
        "status": "matched",
        "events_sha256": "a" * 64,
        "events_payload_id": "events",
        "matches_payload_id": "matches",
        "matches_sha256": "b" * 64,
        "teams": [
            {"external_team_id": "1", "recorded_goals": 1},
            {"external_team_id": "2", "recorded_goals": 0},
        ],
        "reconciliation": {
            "evidence": {
                "teams": [
                    {"external_team_id": "1", "resolved_own_goals": 0},
                    {"external_team_id": "2", "resolved_own_goals": 0},
                ]
            }
        },
    }
    connection = Mock()
    source = Mock()
    source.one.return_value = SimpleNamespace(payload=payload, sha256="a" * 64)
    names = Mock()
    names.all.return_value = [("1", "A"), ("2", "B")]
    connection.execute.side_effect = [source, names]
    return payload, score, connection


def test_match_uses_existing_calculators_and_excludes_shootout():
    _, score, connection = source_and_score()
    with patch(
        "football_analytics.ingestion.tournament_baseline.verify_match_score", return_value=score
    ):
        rows = build_match_baseline(connection, match_id=123)
    assert rows[0].shots == rows[0].goals_for == 1
    assert rows[0].xg == Decimal("0.25")
    assert rows[1].shots == rows[1].goals_for == 0
    assert rows[1].xg == 0
    assert rows[1].xg_against == Decimal("0.25")


def test_missing_xg_is_not_zero():
    payload, score, connection = source_and_score()
    del payload[2]["shot"]["statsbomb_xg"]
    with patch(
        "football_analytics.ingestion.tournament_baseline.verify_match_score", return_value=score
    ):
        with pytest.raises(ValueError, match="Incomplete xG"):
            build_match_baseline(connection, match_id=123)


def test_score_review_prevents_successful_baseline():
    with patch(
        "football_analytics.ingestion.tournament_baseline.verify_match_score",
        return_value={"status": "review"},
    ):
        with pytest.raises(ValueError, match="Score requires review"):
            build_match_baseline(Mock(), match_id=123)
