from unittest.mock import MagicMock, Mock, patch

import pytest
from test_structured_executor import execute, query

from football_analytics.analytics.structured_verification import (
    verify_structured_queries,
    verify_structured_result,
)


def connection_for(result, rows):
    connection = MagicMock()
    metadata = Mock()
    metadata.mappings.return_value.one.return_value = {
        "provider": "statsbomb",
        "sha256": result.source.events_sha256,
        "endpoint": "https://test.invalid/events/123.json",
    }
    values = Mock()
    values.mappings.return_value.all.return_value = rows
    connection.execute.side_effect = [metadata, values]
    return connection


def network_rows():
    return [
        {
            "kind": "node",
            "sender": "101",
            "recipient": None,
            "ids": ["pass"],
            "x": 20.0,
            "y": 40.0,
            "unknown_outcome": False,
            "outside": False,
        },
        {
            "kind": "node",
            "sender": "108",
            "recipient": None,
            "ids": ["c11", "c12"],
            "x": 20.0,
            "y": 40.0,
            "unknown_outcome": False,
            "outside": False,
        },
        {
            "kind": "edge",
            "sender": "101",
            "recipient": "108",
            "ids": ["pass"],
            "x": None,
            "y": None,
            "unknown_outcome": False,
            "outside": False,
        },
    ]


def test_network_compares_ids_connections_and_mean_positions():
    result = execute()
    checked = verify_structured_result(connection_for(result, network_rows()), result)
    assert checked.status == "validated" and checked.comparisons > 0


def test_wrong_mean_is_detected_even_with_same_event_count():
    result = execute()
    rows = network_rows()
    rows[0]["x"] = 21.0
    with pytest.raises(ValueError, match="mean origin"):
        verify_structured_result(connection_for(result, rows), result)


def test_wrong_connection_is_detected():
    result = execute()
    rows = network_rows()
    rows[-1]["recipient"] = "102"
    with pytest.raises(ValueError, match="connections"):
        verify_structured_result(connection_for(result, rows), result)


def map_rows():
    return [
        {
            "id": "c14",
            "kind": "Ball Recovery",
            "x": 10.0,
            "y": 20.0,
            "reason": None,
            "column_index": 1,
            "row_index": 2,
        },
        {
            "id": "clearance",
            "kind": "Clearance",
            "x": 30.0,
            "y": 40.0,
            "reason": None,
            "column_index": 3,
            "row_index": 4,
        },
    ]


def test_map_checks_candidates_coordinates_and_grid_membership():
    result = execute(query(resource_id="analysis.defensive_map"))
    checked = verify_structured_result(connection_for(result, map_rows()), result)
    assert checked.status == "validated"


def test_wrong_grid_cell_is_detected_even_with_same_total():
    result = execute(query(resource_id="analysis.defensive_map"))
    rows = map_rows()
    rows[0]["column_index"] = 2
    with pytest.raises(ValueError, match="grid event membership"):
        verify_structured_result(connection_for(result, rows), result)


def test_unavailable_is_not_counted_as_validated():
    result = execute(query(resource_id="analysis.defensive_map", player_id="112"))
    connection = Mock()
    checked = verify_structured_result(connection, result)
    assert checked.status == "unavailable" and checked.comparisons == 0
    connection.execute.assert_not_called()


def test_batch_continues_after_divergence_and_uses_savepoints():
    q1 = query()
    q2 = query(resource_id="analysis.defensive_map")
    results = [execute(q1), execute(q2)]
    connection = MagicMock()
    with (
        patch(
            "football_analytics.analytics.structured_verification.execute_structured_query",
            side_effect=results,
        ),
        patch(
            "football_analytics.analytics.structured_verification.verify_structured_result",
            side_effect=[ValueError("SQL mismatch"), Mock(status="validated")],
        ),
    ):
        checks = verify_structured_queries(connection, [q1, q2])
    assert checks[0].status == "failed" and checks[1].status == "validated"
    assert connection.begin_nested.call_count == 2


@pytest.mark.parametrize("queries", [[], [query(), query()]])
def test_empty_or_duplicate_batch_rejected(queries):
    with pytest.raises(ValueError):
        verify_structured_queries(Mock(), queries)


def test_partial_network_can_be_validated_with_review():
    from test_structured_executor import source_data

    data = source_data()
    data[1][2]["pass"]["recipient"]["id"] = 102
    result = execute(payload=data)
    rows = network_rows()
    rows[-1]["recipient"] = "102"
    checked = verify_structured_result(connection_for(result, rows), result)
    assert checked.status == "validated_review"
    assert checked.reasons == ("recipient_without_origin",)


def test_map_missing_location_is_checked_without_becoming_failure():
    from test_structured_executor import source_data

    data = source_data()
    next(e for e in data[1] if e["id"] == "clearance").pop("location")
    result = execute(query(resource_id="analysis.defensive_map"), data)
    rows = map_rows()
    rows[1].update(x=None, y=None, reason="missing_location", column_index=None, row_index=None)
    checked = verify_structured_result(connection_for(result, rows), result)
    assert checked.status == "validated_review"
