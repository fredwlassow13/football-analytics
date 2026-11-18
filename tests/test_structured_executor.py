from unittest.mock import MagicMock, Mock, patch

import pytest
from test_complementary_executor import data as complementary_data

from football_analytics.analytics.executor import load_query_sources
from football_analytics.analytics.resources import RESOURCES
from football_analytics.analytics.structured_contracts import (
    MapData,
    NetworkData,
    StructuredQuery,
)
from football_analytics.analytics.structured_executor import (
    execute_structured_query,
    run_structured_query,
)


def source_data():
    ref, events, lineups = complementary_data()

    events[2]["pass"]["recipient"] = {"id": 108}
    next(e for e in events if e["id"] == "c12")["pass"]["outcome"]["name"] = "Incomplete"
    next(e for e in events if e["id"] == "c14")["location"] = [10.0, 20.0]

    events.append(
        {
            "id": "clearance",
            "index": 16,
            "period": 1,
            "timestamp": "00:04:00.000",
            "type": {"name": "Clearance"},
            "team": {"id": 1},
            "player": {"id": 104},
            "location": [30.0, 40.0],
            "clearance": {
                "aerial_won": True,
                "body_part": {"name": "Head"},
            },
        }
    )

    return ref, events, lineups


def query(**changes):
    return StructuredQuery(
        **(
            dict(
                resource_id="analysis.pass_connections",
                match_id=123,
                team_id="1",
            )
            | changes
        )
    )


def execute(q=None, payload=None):
    with (
        patch(
            "football_analytics.analytics.structured_executor.load_query_sources",
            return_value=payload or source_data(),
        ),
        patch(
            "football_analytics.analytics.structured_executor.verify_stored_participation"
        ) as verify,
    ):
        r = execute_structured_query(Mock(), q or query())
        if r.source is not None:
            verify.assert_called_once()
        return r


def test_network_uses_completed_connections_and_all_pass_origins():
    r = execute()

    assert r.status == "available"
    assert isinstance(r.data, NetworkData)
    assert sum(c.completed_passes for c in r.data.connections) == 1
    assert sum(n.pass_attempts for n in r.data.nodes) == 3
    assert {n.external_player_id for n in r.data.nodes} == {"101", "108"}


def test_missing_recipient_position_requires_review():
    d = source_data()
    d[1][2]["pass"]["recipient"]["id"] = 102

    r = execute(payload=d)

    assert r.status == "review"
    assert "recipient_without_origin" in r.reasons


def test_completed_pass_without_recipient_is_unavailable():
    d = source_data()
    d[1][2]["pass"].pop("recipient")

    r = execute(payload=d)

    assert r.status == "unavailable"
    assert r.data is None
    assert r.reasons == ("completed_pass_without_recipient",)


def test_unknown_pass_outcome_is_not_counted_as_completed():
    d = source_data()
    next(e for e in d[1] if e["id"] == "c12")["pass"]["outcome"]["name"] = "Unknown"

    r = execute(payload=d)

    assert "network_pass_outcome_unknown" in r.reasons
    assert sum(c.completed_passes for c in r.data.connections) == 1


def test_invalid_recipient_team_is_rejected():
    d = source_data()
    d[1][2]["pass"]["recipient"]["id"] = 201

    with pytest.raises(ValueError, match="recipient"):
        execute(payload=d)


def test_map_deduplicates_aerial_clearance_and_respects_grid_and_player():
    r = execute(
        query(
            resource_id="analysis.defensive_map",
            player_id="104",
            map_options={"group": "aerials", "columns": 4, "rows": 3},
        )
    )

    assert r.status == "available"
    assert isinstance(r.data, MapData)
    assert r.data.value.candidate_event_ids == ("clearance",)
    assert len(r.data.value.cells) == 12
    assert sum(c.count for c in r.data.value.cells) == 1


def test_missing_map_location_is_preserved_as_review():
    d = source_data()
    next(e for e in d[1] if e["id"] == "clearance").pop("location")

    r = execute(query(resource_id="analysis.defensive_map"), d)

    assert r.status == "review"
    assert "map_location_issues" in r.reasons
    assert r.data.value.candidate_count == 2
    assert r.data.value.located_count == 1


def test_unobserved_reserve_does_not_receive_an_empty_available_map():
    r = execute(query(resource_id="analysis.defensive_map", player_id="112"))

    assert r.status == "unavailable"
    assert r.data is None
    assert r.reasons == ("player_presence_not_established_in_scope",)


@pytest.mark.parametrize(
    "changes",
    [
        {"team_id": "999"},
        {"periods": (3,)},
        {"resource_id": "analysis.defensive_map", "player_id": "201"},
    ],
)
def test_invalid_selection_is_rejected(changes):
    with pytest.raises(ValueError):
        execute(query(**changes))


def test_empty_known_team_and_period_are_valid():
    r = execute(
        query(
            resource_id="analysis.defensive_map",
            team_id="2",
            periods=(2,),
        )
    )
    assert r.status == "available"
    assert r.data.value.candidate_count == 0

    r = execute(query(periods=(2,)))
    assert r.status == "available"
    assert r.data.connections == ()
    assert r.data.nodes == ()


def test_unintegrated_resource_returns_reason_without_database_access():
    connection = Mock()

    r = execute_structured_query(
        connection,
        query(resource_id="analysis.tactical_windows", team_id=None),
    )

    assert r.status == "unavailable"
    assert r.reasons == ("structured_resource_not_integrated",)
    connection.execute.assert_not_called()
    assert (
        next(
            resource for resource in RESOURCES if resource.id == "analysis.tactical_windows"
        ).execution_available
        is False
    )


def test_public_loader_reuses_existing_source_selection():
    connection = Mock()
    payload = source_data()

    with patch(
        "football_analytics.analytics.executor._sources",
        return_value=payload,
    ) as load:
        assert load_query_sources(connection, match_id=123) == payload

    load.assert_called_once_with(connection, 123)


def test_wrapper_uses_read_only_snapshot():
    engine = MagicMock()
    connection = engine.connect.return_value.execution_options.return_value.__enter__.return_value
    expected = execute()

    with patch(
        "football_analytics.analytics.structured_executor.execute_structured_query",
        return_value=expected,
    ):
        assert run_structured_query(engine, query()) == expected

    engine.connect.return_value.execution_options.assert_called_once_with(
        isolation_level="REPEATABLE READ"
    )
    assert "READ ONLY" in str(connection.execute.call_args.args[0])


def test_unobserved_recipient_is_not_assumed_active():
    d = source_data()
    d[1][2]["pass"]["recipient"]["id"] = 112

    r = execute(payload=d)

    assert r.status == "unavailable"
    assert r.reasons == ("recipient_presence_not_established_in_scope",)


@pytest.mark.parametrize(
    "resource,event_id",
    [
        ("analysis.pass_connections", "pass"),
        ("analysis.defensive_map", "clearance"),
    ],
)
def test_unlisted_actor_is_rejected(resource, event_id):
    d = source_data()
    next(e for e in d[1] if e["id"] == event_id)["player"]["id"] = 999

    with pytest.raises(ValueError, match="outside the source roster"):
        execute(query(resource_id=resource), d)
