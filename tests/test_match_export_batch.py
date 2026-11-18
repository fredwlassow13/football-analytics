import hashlib
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from test_match_export import export

from football_analytics.analytics.match_export import write_match_export
from football_analytics.analytics.match_export_batch import (
    ExportedItem,
    ExportIndex,
    FailedItem,
    export_match_batch,
    select_match_ids,
    write_export_index,
)


@pytest.fixture(scope="module")
def sample():
    return export()


def run_batch(sample, output, ids=(123,), *, source=None, build_error=None):
    connection = MagicMock()
    with (
        patch(
            "football_analytics.analytics.match_export_batch.load_query_sources",
            return_value=(source or sample.source, None, None),
        ),
        patch(
            "football_analytics.analytics.match_export_batch.build_match_export",
            return_value=sample,
            side_effect=build_error,
        ) as build,
    ):
        result = export_match_batch(connection, ids, output)
    return result, build, connection


def test_creates_result_and_records_exact_file_hash(sample, tmp_path):
    index, build, connection = run_batch(sample, tmp_path)
    item = index.items[0]
    assert isinstance(item, ExportedItem)
    assert item.status == "created"
    raw = (tmp_path / item.file).read_bytes()
    assert item.sha256 == hashlib.sha256(raw).hexdigest()
    assert item.bytes == len(raw)
    assert item.export_id == sample.export_id
    assert item.source == sample.source
    assert item.structured_statuses["unavailable"] == 1
    assert sum(item.structured_statuses.values()) == len(sample.structured)
    build.assert_called_once_with(connection, 123)
    assert connection.begin_nested.call_count == 1


def test_reuses_existing_current_export_without_recalculation(sample, tmp_path):
    path = tmp_path / "123.json"
    write_match_export(sample, path)
    before = path.read_bytes()
    index, build, _ = run_batch(sample, tmp_path)
    assert index.items[0].status == "reused"
    assert path.read_bytes() == before
    build.assert_not_called()


def test_corrupt_existing_file_is_preserved_and_marked_failed(sample, tmp_path):
    path = tmp_path / "123.json"
    path.write_text("invalid json")
    index, build, _ = run_batch(sample, tmp_path)
    assert isinstance(index.items[0], FailedItem)
    assert path.read_text() == "invalid json"
    build.assert_not_called()


def test_changed_source_prevents_reuse(sample, tmp_path):
    path = tmp_path / "123.json"
    write_match_export(sample, path)
    before = path.read_bytes()
    source = sample.source.model_copy(update={"events_sha256": "f" * 64})
    index, build, _ = run_batch(sample, tmp_path, source=source)
    assert isinstance(index.items[0], FailedItem)
    assert "current match/source" in index.items[0].error
    assert path.read_bytes() == before
    build.assert_not_called()


def test_changed_catalogue_prevents_reuse(sample, tmp_path):
    old = sample.model_copy(update={"catalogue_version": "0.0.0"})
    write_match_export(old, tmp_path / "123.json")
    index, build, _ = run_batch(sample, tmp_path)
    assert isinstance(index.items[0], FailedItem)
    assert "catalogue/profile" in index.items[0].error
    build.assert_not_called()


def test_failed_match_does_not_stop_next_match(sample, tmp_path):
    connection = MagicMock()
    with (
        patch(
            "football_analytics.analytics.match_export_batch.load_query_sources",
            side_effect=[ValueError("source missing"), (sample.source, None, None)],
        ),
        patch(
            "football_analytics.analytics.match_export_batch.build_match_export",
            return_value=sample,
        ),
    ):
        index = export_match_batch(connection, (999, 123), tmp_path)
    assert [item.status for item in index.items] == ["failed", "created"]
    assert not (tmp_path / "999.json").exists()
    assert (tmp_path / "123.json").exists()
    assert connection.begin_nested.call_count == 2


def test_calculation_failure_does_not_create_file(sample, tmp_path):
    index, _, _ = run_batch(sample, tmp_path, build_error=ValueError("calculation failed"))
    assert isinstance(index.items[0], FailedItem)
    assert not (tmp_path / "123.json").exists()


@pytest.mark.parametrize("ids", [(), (123, 123), (0,), (-1,), (True,)])
def test_invalid_selection_fails_before_database_or_writes(ids, tmp_path):
    connection = MagicMock()
    output = tmp_path / "new"
    with pytest.raises(ValueError, match="distinct positive integer"):
        export_match_batch(connection, ids, output)
    connection.begin_nested.assert_not_called()
    assert not output.exists()


def test_index_roundtrip_and_no_overwrite(sample, tmp_path):
    index, _, _ = run_batch(sample, tmp_path)
    path = tmp_path / "index.json"
    write_export_index(index, path)
    assert ExportIndex.model_validate_json(path.read_bytes()) == index
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_export_index(index, path)
    assert path.read_bytes() == before
    assert {p.name for p in tmp_path.iterdir()} == {"123.json", "index.json"}


def test_index_cannot_omit_requested_matches(sample, tmp_path):
    index, _, _ = run_batch(sample, tmp_path)
    incomplete = index.model_copy(update={"items": ()})
    with pytest.raises(ValidationError, match="every requested match"):
        write_export_index(incomplete, tmp_path / "index.json")
    assert not (tmp_path / "index.json").exists()


@pytest.mark.parametrize("ids,expected", [([123, 456], 2), ([123], 2), ([123, 123], 2)])
def test_database_selection_requires_expected_unique_count(ids, expected):
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.all.return_value = [
        {"match_id": str(i)} for i in ids
    ]
    if ids == [123, 456]:
        assert select_match_ids(
            connection, competition_id=43, season_id=106, expected_matches=expected
        ) == (123, 456)
        assert connection.execute.call_args.args[1] == {"competition": "43", "season": "43:106"}
    else:
        with pytest.raises(ValueError, match="Stored match selection"):
            select_match_ids(
                connection, competition_id=43, season_id=106, expected_matches=expected
            )


def test_cli_exports_and_writes_index_in_read_only_snapshot(sample, tmp_path, capsys):
    import sys

    from football_analytics.analytics.match_export_batch import main

    engine = MagicMock()
    argv = [
        "match_export_batch",
        "--database-url",
        "test-url",
        "--competition-id",
        "43",
        "--season-id",
        "106",
        "--expected-matches",
        "1",
        "--output-dir",
        str(tmp_path),
    ]
    with (
        patch.object(sys, "argv", argv),
        patch("sqlalchemy.create_engine", return_value=engine),
        patch(
            "football_analytics.analytics.match_export_batch.select_match_ids", return_value=(123,)
        ),
        patch(
            "football_analytics.analytics.match_export_batch.load_query_sources",
            return_value=(sample.source, None, None),
        ),
        patch(
            "football_analytics.analytics.match_export_batch.build_match_export",
            return_value=sample,
        ),
    ):
        main()
    index = ExportIndex.model_validate_json((tmp_path / "index.json").read_bytes())
    assert index.requested_match_ids == (123,)
    assert index.items[0].status == "created"
    engine.connect.return_value.execution_options.assert_called_once_with(
        isolation_level="REPEATABLE READ"
    )
    connection = engine.connect.return_value.execution_options.return_value.__enter__.return_value
    assert "READ ONLY" in str(connection.execute.call_args.args[0])
    engine.dispose.assert_called_once()
    assert '"created": 1' in capsys.readouterr().out
