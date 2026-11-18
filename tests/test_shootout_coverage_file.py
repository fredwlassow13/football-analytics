import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from football_analytics.ingestion.shootout_coverage_file import (
    load_shootout_coverage_review,
)


def document():
    return {
        "justification": "Review of the supplied source",
        "evidence": ["Disciplinary records inspected"],
        "audit": {
            "events_sha256": "a" * 64,
            "lineups_sha256": "b" * 64,
            "sent_off_player_ids": [],
            "excluded_player_ids": [],
        },
    }


def write_review(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "review.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_loads_reviewed_empty_lists(tmp_path: Path) -> None:
    result = load_shootout_coverage_review(write_review(tmp_path, document()))

    assert result.audit.sent_off_player_ids == ()
    assert result.audit.excluded_player_ids == ()
    assert result.justification == "Review of the supplied source"


def test_omitted_coverage_remains_unknown(tmp_path: Path) -> None:
    data = document()
    del data["audit"]["sent_off_player_ids"]
    del data["audit"]["excluded_player_ids"]

    result = load_shootout_coverage_review(write_review(tmp_path, data))

    assert result.audit.sent_off_player_ids is None
    assert result.audit.excluded_player_ids is None


def test_rejects_missing_evidence(tmp_path: Path) -> None:
    data = document()
    data["evidence"] = []

    with pytest.raises(ValidationError):
        load_shootout_coverage_review(write_review(tmp_path, data))


def test_rejects_unexpected_fields(tmp_path: Path) -> None:
    data = document()
    data["force_eligible"] = True

    with pytest.raises(ValidationError):
        load_shootout_coverage_review(write_review(tmp_path, data))


def test_rejects_blank_justification(tmp_path: Path) -> None:
    data = document()
    data["justification"] = "   "

    with pytest.raises(ValidationError):
        load_shootout_coverage_review(write_review(tmp_path, data))
