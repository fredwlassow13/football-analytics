from unittest.mock import Mock
from uuid import UUID

import pytest
from sqlalchemy import Connection

from football_analytics.ingestion.match_score_verification import verify_match_score


def result(value):
    query = Mock()
    query.mappings.return_value.one_or_none.return_value = value
    query.mappings.return_value.one.return_value = value
    query.mappings.return_value.all.return_value = value
    return query


def inputs(*, recorded_score=1, core_score=None, sql_goal=True, event_source=True):
    payload = [
        {
            "id": "goal-1",
            "index": 1,
            "period": 1,
            "timestamp": "00:01:00.000",
            "type": {"name": "Shot"},
            "team": {"id": 1},
            "player": {"id": 11},
            "shot": {
                "outcome": {"name": "Goal"},
                "type": {"name": "Open Play"},
                "statsbomb_xg": 0.25,
            },
        }
    ]
    catalogue = {
        "match_id": 123,
        "match_date": "2022-01-01",
        "competition": {"competition_id": 43},
        "season": {"season_id": 106},
        "home_team": {"home_team_id": 1},
        "away_team": {"away_team_id": 2},
        "home_score": recorded_score,
        "away_score": 0,
    }
    connection = Mock(spec=Connection)
    connection.execute.side_effect = [
        result(
            {"id": UUID(int=1), "payload": payload, "sha256": "a" * 64} if event_source else None
        ),
        result({"id": UUID(int=2), "sha256": "b" * 64, "match_payload": catalogue}),
        result(
            {
                "home_team": "1",
                "away_team": "2",
                "home_score": recorded_score if core_score is None else core_score,
                "away_score": 0,
            }
        ),
        result([{"event_id": "goal-1", "event_type": "Shot", "team_id": "1"}] if sql_goal else []),
    ]
    return connection


def test_returns_report_with_source_ids_hashes_and_reconciliation() -> None:
    report = verify_match_score(inputs(), match_id=123)

    assert report["match_id"] == 123
    assert report["status"] == "matched"
    assert report["issues"] == []
    assert report["events_payload_id"] == str(UUID(int=1))
    assert report["matches_payload_id"] == str(UUID(int=2))
    assert report["events_sha256"] == "a" * 64
    assert report["matches_sha256"] == "b" * 64
    assert report["sql_goal_evidence_verified"] is True
    assert report["core_score_verified"] is True
    assert report["shot_goals"] == 1
    assert "reconciliation" in report


def test_score_mismatch_is_returned_for_review_by_the_caller() -> None:
    report = verify_match_score(inputs(recorded_score=2), match_id=123)

    assert report["status"] == "mismatch"
    assert report["issues"] == ["recorded_score_mismatch"]


def test_sql_disagreement_still_raises() -> None:
    with pytest.raises(ValueError, match="SQL goal evidence differs"):
        verify_match_score(inputs(sql_goal=False), match_id=123)


def test_core_disagreement_still_raises() -> None:
    with pytest.raises(ValueError, match="Core match differs"):
        verify_match_score(inputs(core_score=2), match_id=123)


def test_missing_event_source_still_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        verify_match_score(inputs(event_source=False), match_id=123)


@pytest.mark.parametrize("match_id", [0, -1, True])
def test_invalid_id_does_not_query_database(match_id) -> None:
    connection = Mock(spec=Connection)

    with pytest.raises(ValueError, match="positive integer"):
        verify_match_score(connection, match_id=match_id)

    connection.execute.assert_not_called()
