import hashlib
import json
from decimal import Decimal
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import pytest

from football_analytics.analytics.catalogue import METRICS
from football_analytics.analytics.contracts import (
    Coverage,
    MetricQuery,
    MetricValue,
    SourceReference,
)
from football_analytics.analytics.executor import combine_values, execute_query, run_query


def match_data():
    events, lineups = [], []
    for team in (1, 2):
        ids = [team * 100 + n for n in range(1, 12)]
        lineups.append(
            {
                "team_id": team,
                "lineup": [
                    {"player_id": p, "player_name": f"P{p}"} for p in [*ids, team * 100 + 12]
                ],
            }
        )
        events.append(
            {
                "id": f"start{team}",
                "index": team,
                "period": 1,
                "timestamp": "00:00:00.000",
                "type": {"name": "Starting XI"},
                "team": {"id": team},
                "tactics": {"lineup": [{"player": {"id": p}} for p in ids]},
            }
        )
    events.extend(
        [
            {
                "id": "pass",
                "index": 3,
                "period": 1,
                "timestamp": "00:01:00.000",
                "type": {"name": "Pass"},
                "team": {"id": 1},
                "player": {"id": 101},
                "pass": {},
            },
            {
                "id": "shot",
                "index": 4,
                "period": 2,
                "timestamp": "00:02:00.000",
                "type": {"name": "Shot"},
                "team": {"id": 1},
                "player": {"id": 101},
                "shot": {
                    "outcome": {"name": "Saved"},
                    "type": {"name": "Open Play"},
                    "statsbomb_xg": 0.25,
                },
            },
            {
                "id": "h1",
                "index": 5,
                "period": 1,
                "timestamp": "00:45:00.000",
                "type": {"name": "Half End"},
            },
            {
                "id": "h2",
                "index": 6,
                "period": 2,
                "timestamp": "00:45:00.000",
                "type": {"name": "Half End"},
            },
            {
                "id": "shootout",
                "index": 7,
                "period": 5,
                "timestamp": "00:00:01.000",
                "type": {"name": "Shot"},
                "team": {"id": 1},
                "player": {"id": 101},
                "shot": {"outcome": {"name": "Goal"}, "statsbomb_xg": 0.8},
            },
        ]
    )

    def sha(obj):
        return hashlib.sha256(json.dumps(obj).encode()).hexdigest()

    ref = SourceReference(
        match_id=123,
        events_payload_id=uuid4(),
        events_sha256=sha(events),
        lineups_payload_id=uuid4(),
        lineups_sha256=sha(lineups),
        participation_calculation_id=uuid4(),
        available_periods=(1, 2),
    )
    return ref, events, lineups


def execute(q, data=None):
    with (
        patch("football_analytics.analytics.executor._sources", return_value=data or match_data()),
        patch("football_analytics.analytics.executor.verify_stored_participation"),
    ):
        return execute_query(Mock(), q)


def query(**updates):
    return MetricQuery(
        **(
            dict(
                match_ids=(123,),
                group_by="player",
                metrics=(
                    "passes.attempted",
                    "passes.completion_pct",
                    "shots.total",
                    "shots.xg",
                    "participation.elapsed_on_field_ms",
                ),
            )
            | updates
        )
    )


def mapping(row):
    return {v.metric_id: v for v in row.values}


def test_existing_calculators_zero_unknown_and_shootout():
    r = execute(query())
    players = {p.player_id: mapping(p) for p in r.rows}
    assert len(players) == 24
    assert players["101"]["passes.attempted"].value == 1
    assert players["101"]["passes.completion_pct"].value == Decimal("100.00")
    assert players["101"]["shots.total"].value == 1
    assert players["101"]["shots.xg"].value == Decimal("0.25")
    assert players["102"]["passes.attempted"].value == 0
    assert players["102"]["shots.xg"].value == Decimal(0)
    assert players["102"]["passes.completion_pct"].status == "not_applicable"
    assert players["112"]["passes.attempted"].status == "unknown"
    assert players["112"]["participation.elapsed_on_field_ms"].value is None
    assert players["101"]["participation.elapsed_on_field_ms"].value == 5_400_000


def test_periods_and_entity_filters():
    r = execute(query(periods=(1,), player_ids=("101",)))
    assert len(r.rows) == 1
    assert mapping(r.rows[0])["shots.total"].value == 0
    assert mapping(r.rows[0])["participation.elapsed_on_field_ms"].value == 2_700_000
    with pytest.raises(ValueError, match="unavailable"):
        execute(query(periods=(3,)))
    with pytest.raises(ValueError, match="not listed"):
        execute(query(player_ids=("missing",)))


def test_team_zero_and_missing_xg_cuts():
    data = match_data()
    del data[1][3]["shot"]["statsbomb_xg"]
    q = query(
        group_by="team",
        metrics=(
            "shots.xg",
            "shots.xg_available",
            "shots.missing_xg",
            "shots.penalty.xg",
            "shots.non_penalty.xg",
        ),
    )
    r = execute(q, data)
    first = mapping(r.rows[0])
    assert first["shots.xg"].status == "incomplete" and first["shots.xg"].value is None
    assert first["shots.xg_available"].status == "incomplete"
    assert first["shots.missing_xg"].value == 1
    assert first["shots.penalty.xg"].value == 0
    assert first["shots.non_penalty.xg"].value is None
    assert mapping(r.rows[1])["shots.xg"].value == 0


def pct(n, d):
    return MetricValue(
        metric_id="passes.completion_pct",
        unit="percent",
        status="available",
        value=(Decimal(n) * 100 / d).quantize(Decimal("0.01")),
        numerator=n,
        denominator=d,
        coverage=Coverage(
            source_complete=True, population="observed", eligible_events=d, covered_events=d
        ),
    )


def test_weighted_aggregation_and_unknown_propagation():
    assert combine_values([pct(1, 1), pct(0, 9)]).value == Decimal("10.00")
    unknown = MetricValue(
        metric_id="passes.completion_pct",
        unit="percent",
        status="unknown",
        value=None,
        reasons=("missing",),
        coverage=Coverage(
            source_complete=True, population="unknown", eligible_events=0, covered_events=0
        ),
    )
    assert combine_values([pct(1, 1), unknown]).status == "unknown"
    with patch("football_analytics.analytics.executor.SUPPORTED_FAMILIES", frozenset({"passes"})):
        with pytest.raises(ValueError):
            execute(query(metrics=("creation.recorded_goal_assists",)))


def test_all_enabled_families_extract_without_dynamic_member_errors():
    metrics = tuple(m.id for m in METRICS if m.family in {"passes", "shots", "xg"})
    assert len(execute(query(group_by="team", metrics=metrics)).rows) == 2


def test_aggregation_preserves_full_selection():
    data = match_data()
    second = (data[0].model_copy(update={"match_id": 124}), data[1], data[2])
    with (
        patch("football_analytics.analytics.executor._sources", side_effect=[data, second]),
        patch("football_analytics.analytics.executor.verify_stored_participation"),
    ):
        r = execute_query(Mock(), query(match_ids=(123, 124), aggregation="selected_matches"))
    player = next(p for p in r.rows if p.player_id == "101")
    assert player.match_ids == (123, 124)
    assert mapping(player)["shots.xg"].value == Decimal("0.50")
    assert mapping(player)["participation.elapsed_on_field_ms"].value == 10_800_000


def test_run_query_sets_consistent_read_only_transaction():
    engine = MagicMock()
    connection = engine.connect.return_value.execution_options.return_value.__enter__.return_value
    with patch("football_analytics.analytics.executor.execute_query", return_value="result"):
        assert run_query(engine, query()) == "result"
    engine.connect.return_value.execution_options.assert_called_once_with(
        isolation_level="REPEATABLE READ"
    )
    assert "READ ONLY" in str(connection.execute.call_args.args[0])


def test_raw_payload_integrity_rejects_tampering():
    from types import SimpleNamespace

    from football_analytics.analytics.executor import _payload

    body = '[{"id": "event"}]'
    digest = hashlib.sha256(body.encode()).hexdigest()
    connection = Mock()
    connection.execute.return_value.one.return_value = SimpleNamespace(
        raw_body=body, payload=json.loads(body), sha256=digest
    )
    assert _payload(connection, uuid4(), digest) == json.loads(body)
    connection.execute.return_value.one.return_value.raw_body = "[]"
    with pytest.raises(ValueError, match="body/hash mismatch"):
        _payload(connection, uuid4(), digest)
    connection.execute.return_value.one.return_value.raw_body = body
    connection.execute.return_value.one.return_value.payload = []
    with pytest.raises(ValueError, match="JSON differs"):
        _payload(connection, uuid4(), digest)


def test_source_requires_calculation_for_current_hashes():
    from football_analytics.analytics.executor import _sources

    connection = Mock()
    connection.execute.return_value.one_or_none.return_value = None
    with patch(
        "football_analytics.analytics.executor.load_latest_match_payload",
        return_value={"sha256": "a" * 64},
    ):
        with pytest.raises(ValueError, match="bound to current sources"):
            _sources(connection, 123)
    parameters = connection.execute.call_args.args[1]
    assert parameters["events"] == parameters["lineups"] == "a" * 64


def test_sources_use_exact_calculation_payload_ids():
    from types import SimpleNamespace

    from football_analytics.analytics.executor import _sources

    ref, events, lineups = match_data()
    calc = SimpleNamespace(
        id=ref.participation_calculation_id,
        events_payload_id=ref.events_payload_id,
        lineups_payload_id=ref.lineups_payload_id,
        events_sha256=ref.events_sha256,
        lineups_sha256=ref.lineups_sha256,
    )
    connection = Mock()
    result = Mock()
    result.one_or_none.return_value = calc
    endpoint_a, endpoint_b = Mock(), Mock()
    endpoint_a.scalar_one.return_value = "https://test.invalid/events/123.json"
    endpoint_b.scalar_one.return_value = "https://test.invalid/lineups/123.json"
    connection.execute.side_effect = [result, endpoint_a, endpoint_b]
    with (
        patch(
            "football_analytics.analytics.executor.load_latest_match_payload",
            return_value={"sha256": "a" * 64},
        ),
        patch(
            "football_analytics.analytics.executor._payload", side_effect=[events, lineups]
        ) as load,
    ):
        source, _, _ = _sources(connection, 123)
    assert source == ref
    assert load.call_args_list[0].args[1] == calc.events_payload_id
    assert load.call_args_list[1].args[1] == calc.lineups_payload_id


def test_unknown_family_rejected_before_database_access():
    connection = Mock()
    with patch("football_analytics.analytics.executor.SUPPORTED_FAMILIES", frozenset({"passes"})):
        with pytest.raises(ValueError, match="not connected"):
            execute_query(connection, query(metrics=("creation.recorded_goal_assists",)))
    connection.execute.assert_not_called()


def test_partial_xg_aggregation_does_not_become_complete():
    data = match_data()
    del data[1][3]["shot"]["statsbomb_xg"]
    r = execute(query(group_by="team", metrics=("shots.xg", "shots.xg_available")), data)
    v = mapping(r.rows[0])
    assert combine_values([v["shots.xg"], v["shots.xg"]]).status == "incomplete"
    assert combine_values([v["shots.xg"], v["shots.xg"]]).value is None
    assert combine_values([v["shots.xg_available"], v["shots.xg_available"]]).value is None


def test_empty_aggregate_rejected_explicitly():
    with pytest.raises(ValueError, match="empty metric selection"):
        combine_values([])


def test_incomplete_percentage_without_components_stays_null():
    partial = MetricValue(
        metric_id="passes.completion_pct",
        unit="percent",
        value=None,
        status="incomplete",
        reasons=("missing_components",),
        coverage=Coverage(
            source_complete=False, population="observed", eligible_events=1, covered_events=0
        ),
    )
    combined = combine_values([partial, pct(1, 2)])
    assert combined.status == "incomplete" and combined.value is None
    assert combine_values([partial]).value is None
