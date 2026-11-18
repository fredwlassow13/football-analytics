import os
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Connection, create_engine, text

from football_analytics.ingestion import match_score_batch as batch


def test_failure_does_not_stop_next_match_and_preserves_order(monkeypatch) -> None:
    calls = []

    def verify(connection, *, match_id):
        calls.append(match_id)
        if match_id == 1:
            raise ValueError("Source missing")
        return {"match_id": match_id, "status": "matched", "events_sha256": "a" * 64}

    monkeypatch.setattr(batch, "verify_match_score", verify)
    result = batch.verify_match_scores(MagicMock(spec=Connection), [1, 2])

    assert calls == [1, 2]
    assert result.requested_match_ids == (1, 2)
    assert [item.status for item in result.items] == ["failed", "success"]
    assert result.items[0].error_type == "ValueError"
    assert result.items[0].error_message == "Source missing"
    assert result.items[1].report["events_sha256"] == "a" * 64
    assert result.failed_count == result.success_count == 1
    assert result.review_count == 0
    assert batch.MatchScoreBatchResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("status", ["mismatch", "review"])
def test_score_diagnostics_are_review_not_execution_failure(monkeypatch, status) -> None:
    monkeypatch.setattr(
        batch,
        "verify_match_score",
        lambda connection, *, match_id: {
            "match_id": match_id,
            "status": status,
            "issues": ["needs_review"],
        },
    )
    result = batch.verify_match_scores(MagicMock(spec=Connection), [1])

    assert result.review_count == 1
    assert result.failed_count == 0
    assert result.items[0].report["status"] == status
    assert result.items[0].error_type is None


def test_unknown_verifier_status_is_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        batch,
        "verify_match_score",
        lambda connection, *, match_id: {
            "match_id": match_id,
            "status": "unexpected",
        },
    )
    result = batch.verify_match_scores(MagicMock(spec=Connection), [1])
    assert result.failed_count == 1
    assert "unsupported" in result.items[0].error_message


def test_wrong_report_identity_is_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        batch,
        "verify_match_score",
        lambda connection, *, match_id: {
            "match_id": 999,
            "status": "matched",
        },
    )
    result = batch.verify_match_scores(MagicMock(spec=Connection), [1])
    assert result.failed_count == 1
    assert "identity" in result.items[0].error_message


@pytest.mark.parametrize("ids", [[], [1, 1], [0], [-1], [True]])
def test_invalid_selection_never_executes_a_match(monkeypatch, ids) -> None:
    verifier = MagicMock()
    connection = MagicMock(spec=Connection)
    monkeypatch.setattr(batch, "verify_match_score", verifier)
    with pytest.raises(ValueError, match="unique positive"):
        batch.verify_match_scores(connection, ids)
    verifier.assert_not_called()
    connection.begin_nested.assert_not_called()


def test_postgresql_error_rolls_back_savepoint_before_next_match(monkeypatch) -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL tests")

    def verify(connection, *, match_id):
        if match_id == 1:
            connection.execute(text("SELECT 1 / 0"))
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
        return {"match_id": match_id, "status": "matched"}

    monkeypatch.setattr(batch, "verify_match_score", verify)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            with connection.begin():
                result = batch.verify_match_scores(connection, [1, 2])
                assert connection.execute(text("SELECT 1")).scalar_one() == 1
        assert [item.status for item in result.items] == ["failed", "success"]
        assert result.items[0].error_type == "DataError"
    finally:
        engine.dispose()
