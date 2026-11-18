from decimal import Decimal
from unittest.mock import MagicMock, Mock, patch

import pytest
from pydantic import ValidationError
from test_structured_executor import source_data

from football_analytics.analytics.catalogue import METRICS
from football_analytics.analytics.match_export import (
    MatchExport,
    build_match_export,
    run_match_export,
    write_match_export,
)


def export(payload=None):
    payload = payload or source_data()
    for event in payload[1][:2]:
        event["tactics"]["formation"] = 442
        for number, player in enumerate(event["tactics"]["lineup"]):
            player["position"] = {
                "id": 1 if number == 0 else 2,
                "name": "Goalkeeper" if number == 0 else "Right Back",
            }
    with (
        patch("football_analytics.analytics.executor._sources", return_value=payload),
        patch("football_analytics.analytics.executor.verify_stored_participation"),
        patch("football_analytics.analytics.structured_executor.verify_stored_participation"),
    ):
        return build_match_export(Mock(), 123)


def test_all_enabled_metrics_and_structures_are_exported():
    result = export()
    assert {d.id for d in result.definitions} == {d.id for d in METRICS if d.execution_available}
    assert len(result.team_metrics.rows) == 2
    assert len(result.player_metrics.rows) == 24
    assert len(result.structured) == 19
    assert sum(r.query.resource_id == "analysis.defensive_map" for r in result.structured) == 14
    assert (
        next(
            r for r in result.structured if r.query.resource_id == "analysis.tactical_windows"
        ).status
        == "unavailable"
    )
    assert result.validation_report_included is False


def test_missing_values_and_quality_warnings_survive_json_roundtrip(tmp_path):
    result = export()
    path = tmp_path / "123.json"
    write_match_export(result, path)
    loaded = MatchExport.model_validate_json(path.read_text())
    assert loaded == result
    reserve = next(r for r in loaded.player_metrics.rows if r.player_id == "112")
    attempted = next(v for v in reserve.values if v.metric_id == "passes.attempted")
    assert attempted.value is None
    assert attempted.status == "unknown"
    assert attempted.reasons
    xg = next(
        v
        for r in loaded.player_metrics.rows
        if r.player_id == "102"
        for v in r.values
        if v.metric_id == "shots.xg"
    )
    assert isinstance(xg.value, Decimal)
    attacks = next(r for r in loaded.structured if r.query.resource_id == "analysis.attacks")
    assert attacks.status == "review" and attacks.reasons
    assert attacks.data.value.possession_duration_ms is None


def test_sources_cannot_be_mixed():
    result = export()
    first = result.structured[0]
    source = first.source.model_copy(update={"events_sha256": "f" * 64})
    changed = first.model_copy(update={"source": source})
    with pytest.raises(ValidationError):
        MatchExport.model_validate_json(
            result.model_copy(
                update={"structured": (changed, *result.structured[1:])}
            ).model_dump_json()
        )


def test_missing_query_or_population_is_rejected():
    result = export()
    with pytest.raises(ValidationError, match="structured queries"):
        MatchExport.model_validate_json(
            result.model_copy(update={"structured": result.structured[:-1]}).model_dump_json()
        )
    metrics = result.player_metrics.model_copy(update={"rows": result.player_metrics.rows[:-1]})
    with pytest.raises(ValidationError, match="population"):
        MatchExport.model_validate_json(
            result.model_copy(update={"player_metrics": metrics}).model_dump_json()
        )


def test_existing_output_is_preserved(tmp_path):
    result = export()
    output = tmp_path / "existing.json"
    output.write_text("existing content")
    with pytest.raises(FileExistsError):
        write_match_export(result, output)
    assert output.read_text() == "existing content"
    assert list(tmp_path.iterdir()) == [output]


def test_invalid_result_does_not_create_file(tmp_path):
    result = export().model_copy(update={"match_id": 999})
    output = tmp_path / "invalid.json"
    with pytest.raises(ValidationError):
        write_match_export(result, output)
    assert not output.exists()


def test_wrapper_uses_one_read_only_snapshot():
    engine = MagicMock()
    expected = Mock()
    with patch(
        "football_analytics.analytics.match_export.build_match_export", return_value=expected
    ) as build:
        assert run_match_export(engine, 123) is expected
    engine.connect.return_value.execution_options.assert_called_once_with(
        isolation_level="REPEATABLE READ"
    )
    connection = engine.connect.return_value.execution_options.return_value.__enter__.return_value
    assert "READ ONLY" in str(connection.execute.call_args.args[0])
    build.assert_called_once_with(connection, 123)
