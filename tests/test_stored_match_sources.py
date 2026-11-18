import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from football_analytics.ingestion.stored_match_sources import (
    load_latest_match_payload,
)


@pytest.mark.parametrize("match_id", [0, -1, True])
def test_rejects_invalid_id_before_query(match_id) -> None:
    connection = Mock()

    with pytest.raises(ValueError, match="positive integer"):
        load_latest_match_payload(
            connection,
            match_id=match_id,
            kind="events",
        )

    connection.execute.assert_not_called()


@pytest.fixture
def stored_sources():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL tests")

    engine = create_engine(url)

    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            try:
                run_id = uuid4()
                match_id = uuid4().int % 100000000000 + 1

                connection.execute(
                    text("""
                        INSERT INTO raw.ingestion_runs (
                            id, provider, pipeline_name, status
                        )
                        VALUES (
                            :id, 'statsbomb', 'test_source_selection',
                            'success'
                        )
                    """),
                    {"id": run_id},
                )

                timestamp = datetime(2026, 1, 1, tzinfo=UTC)
                rows = []

                entries = [
                    ("events", match_id, "old", 0),
                    ("events", match_id, "new", 1),
                    ("lineups", match_id, "lineups", 2),
                    ("events", match_id + 1, "other-match", 3),
                ]

                for kind, external_id, marker, offset in entries:
                    body = json.dumps([{"marker": marker}])

                    rows.append(
                        {
                            "id": uuid4(),
                            "endpoint": (f"https://test.invalid/data/{kind}/{external_id}.json"),
                            "payload": body,
                            "raw_body": body,
                            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                            "fetched_at": timestamp + timedelta(seconds=offset),
                            "run_id": run_id,
                        }
                    )

                connection.execute(
                    text("""
                        INSERT INTO raw.provider_payloads (
                            id, provider, endpoint, payload, raw_body,
                            sha256, fetched_at, ingestion_run_id
                        )
                        VALUES (
                            :id, 'statsbomb', :endpoint,
                            CAST(:payload AS jsonb), :raw_body,
                            :sha256, :fetched_at, :run_id
                        )
                    """),
                    rows,
                )

                yield connection, match_id, rows
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def test_selects_latest_source_for_requested_match_and_kind(
    stored_sources,
) -> None:
    connection, match_id, rows = stored_sources

    events = load_latest_match_payload(
        connection,
        match_id=match_id,
        kind="events",
    )
    lineups = load_latest_match_payload(
        connection,
        match_id=match_id,
        kind="lineups",
    )

    assert events["id"] == rows[1]["id"]
    assert events["payload"] == [{"marker": "new"}]
    assert events["sha256"] == rows[1]["sha256"]

    assert lineups["id"] == rows[2]["id"]
    assert lineups["payload"] == [{"marker": "lineups"}]


def test_missing_source_does_not_select_another_match(
    stored_sources,
) -> None:
    connection, match_id, _ = stored_sources

    with pytest.raises(ValueError, match="not found"):
        load_latest_match_payload(
            connection,
            match_id=match_id + 2,
            kind="events",
        )
