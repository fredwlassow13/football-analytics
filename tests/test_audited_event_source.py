import hashlib
import json
from pathlib import Path

import pytest

from football_analytics.ingestion.audited_event_source import (
    load_audited_event_source,
)


def write_source(
    root: Path,
    *,
    endpoint="https://test.invalid/data/events/123.json",
):
    body = '[{"id": "evento-á"}]\n'.encode()
    path = root / "sources" / "events" / "123.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(body)

    manifest = {
        "files": {
            "events/123.json": {
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

    return body, path


def test_preserves_original_bytes_endpoint_and_hash(tmp_path: Path) -> None:
    body, _ = write_source(tmp_path)

    result = load_audited_event_source(tmp_path, match_id=123)

    assert result.match_id == 123
    assert result.body == body
    assert result.endpoint == "https://test.invalid/data/events/123.json"
    assert result.sha256 == hashlib.sha256(body).hexdigest()


def test_rejects_modified_source_file(tmp_path: Path) -> None:
    _, path = write_source(tmp_path)
    path.write_bytes(b"[]")

    with pytest.raises(ValueError, match="hash differs"):
        load_audited_event_source(tmp_path, match_id=123)


def test_rejects_match_missing_from_manifest(tmp_path: Path) -> None:
    write_source(tmp_path)

    with pytest.raises(ValueError, match="unavailable"):
        load_audited_event_source(tmp_path, match_id=456)


def test_rejects_endpoint_for_another_match(tmp_path: Path) -> None:
    write_source(
        tmp_path,
        endpoint="https://test.invalid/data/events/456.json",
    )

    with pytest.raises(ValueError, match="endpoint differs"):
        load_audited_event_source(tmp_path, match_id=123)


@pytest.mark.parametrize("match_id", [0, -1, True])
def test_rejects_invalid_id_before_reading_files(
    tmp_path: Path,
    match_id,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        load_audited_event_source(tmp_path, match_id=match_id)
