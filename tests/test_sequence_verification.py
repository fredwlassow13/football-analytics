from unittest.mock import Mock, patch

import pytest
from test_structured_executor import execute, source_data
from test_structured_sequences import query
from test_structured_verification import connection_for

from football_analytics.analytics.structured_verification import (
    verify_structured_queries,
    verify_structured_result,
)


def prepare(resource="analysis.attacks"):
    payload = source_data()
    result = execute(query(resource), payload)
    rows = [
        {"event": event}
        for event in sorted(
            payload[1],
            key=lambda event: event["index"],
        )
    ]
    return result, rows


@pytest.mark.parametrize(
    "resource",
    ["analysis.possession_fragments", "analysis.attacks"],
)
def test_sequences_verify_source_and_preserve_review(resource):
    result, rows = prepare(resource)

    checked = verify_structured_result(
        connection_for(result, rows),
        result,
    )

    assert checked.status == "validated_review"
    assert checked.reasons == result.reasons
    assert checked.source == result.source
    assert checked.comparisons > 10


def test_same_count_wrong_source_event_is_detected():
    result, rows = prepare()
    rows[2]["event"]["id"] = "another-event"

    with pytest.raises(ValueError, match="playing event IDs"):
        verify_structured_result(
            connection_for(result, rows),
            result,
        )


def test_fragment_membership_is_checked_independently():
    result, rows = prepare("analysis.possession_fragments")
    first = result.data.value.fragments[0]

    changed = first.model_copy(update={"external_event_ids": ("wrong",)})
    value = result.data.value.model_copy(
        update={
            "fragments": (
                changed,
                *result.data.value.fragments[1:],
            )
        }
    )
    result = result.model_copy(update={"data": result.data.model_copy(update={"value": value})})

    with pytest.raises(ValueError, match="fragment event IDs"):
        verify_structured_result(
            connection_for(result, rows),
            result,
        )


def test_shootout_records_must_be_excluded():
    result, rows = prepare()

    shootout = next(row["event"] for row in rows if row["event"]["period"] == 5)
    shootout["id"] = "wrong-shootout"

    with pytest.raises(ValueError, match="excluded shootout IDs"):
        verify_structured_result(
            connection_for(result, rows),
            result,
        )


def test_replay_detects_changed_stoppages():
    payload = source_data()
    payload[1][2]["out"] = True
    payload[1][2]["duration"] = 1.0

    result = execute(query("analysis.attacks"), payload)
    rows = [
        {"event": event}
        for event in sorted(
            payload[1],
            key=lambda event: event["index"],
        )
    ]

    assert result.data.value.stoppages

    value = result.data.value.model_copy(update={"stoppages": ()})
    result = result.model_copy(update={"data": result.data.model_copy(update={"value": value})})

    with pytest.raises(
        ValueError,
        match="complete deterministic replay",
    ):
        verify_structured_result(
            connection_for(result, rows),
            result,
        )


def test_batch_records_sequence_divergence_as_failed():
    result, rows = prepare()
    rows[2]["event"]["id"] = "wrong"
    connection = connection_for(result, rows)

    with patch(
        "football_analytics.analytics.structured_verification.execute_structured_query",
        return_value=result,
    ):
        checked = verify_structured_queries(
            connection,
            [result.query],
        )

    assert checked[0].status == "failed"
    assert "playing event IDs" in checked[0].error


def test_unavailable_sequences_do_not_receive_validation():
    result, _ = prepare()
    result = result.model_copy(
        update={
            "status": "unavailable",
            "data": None,
            "reasons": ("missing_source",),
        }
    )
    connection = Mock()

    checked = verify_structured_result(connection, result)

    assert checked.status == "unavailable"
    assert checked.comparisons == 0
    connection.execute.assert_not_called()
