from uuid import uuid4

import pytest
from pydantic import ValidationError

from football_analytics.analytics.contracts import SourceReference
from football_analytics.analytics.resources import RESOURCES
from football_analytics.analytics.structured_contracts import (
    AttackData,
    MapData,
    NetworkData,
    PossessionData,
    StructuredQuery,
    StructuredResult,
    WindowsData,
)
from football_analytics.ingestion.attack_sequences import AttackSequenceResult
from football_analytics.ingestion.defensive_map import build_defensive_map
from football_analytics.ingestion.pass_network import PassConnection
from football_analytics.ingestion.pass_network_nodes import PassNetworkNode
from football_analytics.ingestion.possession_sequences import PossessionSequenceResult
from football_analytics.ingestion.tactical_window_catalogue import TacticalWindow


def source(**changes):
    return SourceReference(
        **(
            dict(
                match_id=123,
                events_payload_id=uuid4(),
                events_sha256="a" * 64,
                available_periods=(1, 2),
            )
            | changes
        )
    )


def query(**changes):
    return StructuredQuery(
        **(dict(resource_id="analysis.pass_connections", match_id=123, team_id="10") | changes)
    )


def result(**changes):
    return StructuredResult(
        **(
            dict(
                query=query(),
                source=source(),
                status="available",
                data=NetworkData(periods=(1, 2), nodes=(), connections=()),
            )
            | changes
        )
    )


def test_network_json_roundtrip_and_source_identity():
    r = result()
    assert StructuredResult.model_validate_json(r.model_dump_json()) == r

    with pytest.raises(ValidationError, match="another match"):
        result(source=source(match_id=124))

    with pytest.raises(ValidationError, match="unavailable"):
        result(query=query(periods=(3,)))


@pytest.mark.parametrize(
    "changes",
    [
        {"match_id": True},
        {"periods": (5,)},
        {"periods": (1, 1)},
        {"team_id": " "},
        {"team_id": None},
        {"player_id": "101"},
        {"map_options": {}},
        {"aggregation": "selected_matches"},
        {"resource_id": "analysis.attacks", "team_id": None, "periods": (1,)},
    ],
)
def test_unsupported_queries_are_rejected(changes):
    with pytest.raises(ValidationError):
        query(**changes)


def test_unavailable_requires_reason_and_no_payload():
    r = StructuredResult(query=query(), status="unavailable", reasons=("source_missing",))
    assert r.data is None

    with pytest.raises(ValidationError):
        StructuredResult(query=query(), status="unavailable")

    with pytest.raises(ValidationError):
        result(status="unavailable", reasons=("source_missing",))


def test_partial_network_requires_review_without_inventing_recipient_position():
    node = PassNetworkNode(
        provider="statsbomb",
        external_match_id="123",
        external_team_id="10",
        external_player_id="101",
        mean_x=20.0,
        mean_y=40.0,
        external_event_ids=("pass1",),
    )
    edge = PassConnection(
        provider="statsbomb",
        external_match_id="123",
        external_team_id="10",
        external_sender_player_id="101",
        external_recipient_player_id="102",
        external_event_ids=("pass1",),
    )
    data = NetworkData(periods=(1, 2), nodes=(node,), connections=(edge,))

    with pytest.raises(ValidationError, match="review status"):
        result(data=data)

    assert result(data=data, status="review", reasons=("recipient_without_origin",))

    with pytest.raises(ValidationError, match="Duplicate network"):
        result(data=NetworkData(periods=(1, 2), nodes=(node, node), connections=()))


def test_map_options_and_hash_are_checked():
    value = build_defensive_map(
        [],
        [],
        [],
        provider="statsbomb",
        match_id="123",
        events_sha256="a" * 64,
        team_id="10",
        available_periods=(1, 2),
    )
    q = query(resource_id="analysis.defensive_map")

    assert result(query=q, data=MapData(value=value))

    with pytest.raises(ValidationError, match="provenance"):
        result(query=q, data=MapData(value=value), source=source(events_sha256="b" * 64))

    with pytest.raises(ValidationError, match="Map options"):
        result(
            query=query(resource_id="analysis.defensive_map", map_options={"columns": 6}),
            data=MapData(value=value),
        )


def test_fragments_and_attacks_keep_native_outputs_and_null_possession():
    header = dict(
        provider="statsbomb",
        external_match_id="123",
        events_sha256="a" * 64,
        available_periods=(1, 2),
        fragments=(),
        issues=(),
        external_event_ids=(),
        excluded_shootout_event_ids=(),
    )
    fragments = PossessionSequenceResult(**header)

    assert result(
        query=query(resource_id="analysis.possession_fragments", team_id=None),
        data=PossessionData(value=fragments),
    )

    attacks = AttackSequenceResult(
        **header, attacks=(), boundary_evidence=(), stoppages=(), unassigned_event_ids=()
    )

    assert attacks.possession_duration_ms is None
    assert attacks.possession_percentage is None

    assert result(
        query=query(resource_id="analysis.attacks", team_id=None),
        data=AttackData(value=attacks),
    )

    with pytest.raises(ValidationError, match="requested resource"):
        result(data=AttackData(value=attacks))


def test_zero_duration_windows_remain_valid_and_duplicates_are_rejected():
    windows = tuple(
        TacticalWindow(
            provider="statsbomb",
            external_match_id="123",
            period=p,
            number_in_period=1,
            start_elapsed_ms=0,
            end_elapsed_ms=0,
            start_source_index=p,
            end_source_index=p + 1,
            start_reasons=("period_start",),
            start_event_ids=(),
        )
        for p in (1, 2)
    )
    q = query(resource_id="analysis.tactical_windows", team_id=None)

    assert result(query=q, data=WindowsData(windows=windows))

    with pytest.raises(ValidationError, match="Duplicate tactical"):
        result(query=q, data=WindowsData(windows=windows + windows))


def test_only_validated_structured_resources_are_enabled():
    assert {resource.id for resource in RESOURCES if resource.execution_available} == {
        "analysis.pass_connections",
        "analysis.defensive_map",
    }
