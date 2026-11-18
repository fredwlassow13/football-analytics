import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from football_analytics.ingestion.import_audited_events import (
    import_audited_events,
)
from football_analytics.integrations.base import ProviderUnavailable


@pytest.fixture
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL tests")

    database = create_engine(url)

    try:
        yield database
    finally:
        database.dispose()


def write_source(root: Path, match_id: int, payload):
    body = json.dumps(payload).encode("utf-8")
    relative = f"events/{match_id}.json"
    path = root / "sources" / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(body)

    endpoint = f"https://test.invalid/data/{relative}"
    manifest = {
        "files": {
            relative: {
                "status": 200,
                "url": endpoint,
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        }
    }

    (root / "downloads.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    return body, path, endpoint


def valid_payload():
    return [
        {
            "id": "end-1",
            "index": 1,
            "period": 1,
            "timestamp": "00:45:00.000",
            "type": {"name": "Half End"},
        },
        {
            "id": "end-2",
            "index": 2,
            "period": 2,
            "timestamp": "00:45:00.000",
            "type": {"name": "Half End"},
        },
    ]


def test_repeated_import_preserves_separate_runs_and_original_hash(
    engine,
    tmp_path,
) -> None:
    match_id = uuid4().int % 100000000000 + 1
    body, _, endpoint = write_source(
        tmp_path,
        match_id,
        valid_payload(),
    )

    first = import_audited_events(
        engine,
        audit_dir=tmp_path,
        match_id=match_id,
    )
    second = import_audited_events(
        engine,
        audit_dir=tmp_path,
        match_id=match_id,
    )

    assert first != second

    with engine.connect() as connection:
        rows = (
            connection.execute(
                text("""
                SELECT p.endpoint, p.sha256, r.status, r.records_processed
                FROM raw.provider_payloads p
                JOIN raw.ingestion_runs r ON r.id = p.ingestion_run_id
                WHERE r.id IN (:first, :second)
            """),
                {"first": first, "second": second},
            )
            .mappings()
            .all()
        )

    assert len(rows) == 2
    assert all(row["endpoint"] == endpoint for row in rows)
    assert all(row["sha256"] == hashlib.sha256(body).hexdigest() for row in rows)
    assert all(row["status"] == "success" for row in rows)
    assert all(row["records_processed"] == 2 for row in rows)


def test_invalid_payload_is_preserved_with_failed_run(
    engine,
    tmp_path,
) -> None:
    match_id = uuid4().int % 100000000000 + 1
    _, _, endpoint = write_source(
        tmp_path,
        match_id,
        {"invalid": True},
    )

    with pytest.raises(ProviderUnavailable):
        import_audited_events(
            engine,
            audit_dir=tmp_path,
            match_id=match_id,
        )

    with engine.connect() as connection:
        row = (
            connection.execute(
                text("""
                SELECT r.status, p.payload, r.records_failed
                FROM raw.provider_payloads p
                JOIN raw.ingestion_runs r ON r.id = p.ingestion_run_id
                WHERE p.endpoint = :endpoint
            """),
                {"endpoint": endpoint},
            )
            .mappings()
            .one()
        )

    assert row["status"] == "failed"
    assert row["records_failed"] == 1
    assert row["payload"] == {"invalid": True}


def test_hash_failure_is_audited_without_importing_modified_source(
    engine,
    tmp_path,
) -> None:
    match_id = uuid4().int % 100000000000 + 1
    _, path, _ = write_source(tmp_path, match_id, valid_payload())
    path.write_bytes(b"[]")

    with pytest.raises(ValueError, match="hash differs"):
        import_audited_events(
            engine,
            audit_dir=tmp_path,
            match_id=match_id,
        )

    with engine.connect() as connection:
        row = (
            connection.execute(
                text("""
                SELECT
                    r.status,
                    (
                        SELECT count(*)
                        FROM raw.provider_payloads p
                        WHERE p.ingestion_run_id = r.id
                    ) AS payload_count
                FROM raw.ingestion_runs r
                JOIN raw.ingestion_errors e ON e.ingestion_run_id = r.id
                WHERE r.pipeline_name = 'audited_match_events'
                  AND e.message LIKE :prefix
            """),
                {"prefix": f"match_id={match_id};%"},
            )
            .mappings()
            .one()
        )

    assert row["status"] == "failed"
    assert row["payload_count"] == 0
