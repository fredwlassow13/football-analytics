import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import pytest
from test_score_state_execution import data, execute

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_score_state_database.py"
SPEC = importlib.util.spec_from_file_location("score_state_db_verifier", MODULE_PATH)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def fixture(tmp_path):
    source = data()
    path = tmp_path / "123.json"
    path.write_text(json.dumps(source[1]))
    match = {
        "match_id": 123,
        "home_team": {"home_team_id": 1},
        "away_team": {"away_team_id": 2},
        "home_score": 1,
        "away_score": 1,
    }
    return path, match, source


def fake_result(source):
    return lambda _connection, q: execute(q, source)


def test_complete_comparison_uses_three_database_queries_and_42_metrics(tmp_path):
    path, match, source = fixture(tmp_path)
    with patch.object(verifier, "execute_query", side_effect=fake_result(source)) as queries:
        result = verifier.verify_match(Mock(), path, match)
    assert queries.call_count == 3
    assert result["comparisons"] == 42
    assert result["events_sha256"] == source[0].events_sha256
    assert len(result["checks"]) == 42
    assert {c["score_state"] for c in result["checks"]} == set(verifier.STATES)
    assert result["participation_calculation_id"] == str(source[0].participation_calculation_id)


def test_wrong_scalar_value_is_detected(tmp_path):
    path, match, source = fixture(tmp_path)

    def wrong(connection, q):
        result = execute(q, source)
        row = result.rows[0]
        value = row.values[0].model_copy(update={"value": 999})
        return result.model_copy(
            update={
                "rows": (
                    row.model_copy(update={"values": (value, *row.values[1:])}),
                    *result.rows[1:],
                )
            }
        )

    with patch.object(verifier, "execute_query", side_effect=wrong):
        with pytest.raises(ValueError, match="Mismatch"):
            verifier.verify_match(Mock(), path, match)


def test_database_hash_must_match_independent_file(tmp_path):
    path, match, source = fixture(tmp_path)

    def wrong(connection, q):
        result = execute(q, source)
        return result.model_copy(
            update={
                "sources": (result.sources[0].model_copy(update={"events_sha256": "f" * 64}),),
                "score_evidence": (
                    result.score_evidence[0].model_copy(update={"events_sha256": "f" * 64}),
                ),
            }
        )

    with patch.object(verifier, "execute_query", side_effect=wrong):
        with pytest.raises(ValueError, match="source differs"):
            verifier.verify_match(Mock(), path, match)


def test_changing_source_identity_between_states_is_detected(tmp_path):
    path, match, source = fixture(tmp_path)

    def changed(connection, q):
        result = execute(q, source)
        return result.model_copy(
            update={
                "sources": (result.sources[0].model_copy(update={"events_payload_id": uuid4()}),)
            }
        )

    with patch.object(verifier, "execute_query", side_effect=changed):
        with pytest.raises(ValueError, match="changed between"):
            verifier.verify_match(Mock(), path, match)


def test_missing_lineup_provenance_is_detected(tmp_path):
    path, match, source = fixture(tmp_path)

    def wrong(connection, q):
        result = execute(q, source)
        return result.model_copy(
            update={
                "sources": (
                    result.sources[0].model_copy(
                        update={
                            "lineups_payload_id": None,
                            "lineups_sha256": None,
                            "participation_calculation_id": None,
                        }
                    ),
                )
            }
        )

    with patch.object(verifier, "execute_query", side_effect=wrong):
        with pytest.raises(ValueError, match="provenance"):
            verifier.verify_match(Mock(), path, match)


def test_ordinary_result_cannot_substitute_for_score_filtered_result(tmp_path):
    path, match, _ = fixture(tmp_path)
    with patch.object(verifier, "execute_query", return_value=Mock(spec=[])):
        with pytest.raises(ValueError, match="score-state result"):
            verifier.verify_match(Mock(), path, match)


def test_independent_oracle_checks_pre_goal_state_and_excludes_shootout(tmp_path):
    path, match, source = fixture(tmp_path)
    raw = json.loads(path.read_text())
    raw.append(
        {
            "id": "shootout",
            "index": 999,
            "period": 5,
            "team": {"id": 1},
            "type": {"name": "Shot"},
            "shot": {"outcome": {"name": "Goal"}, "statsbomb_xg": 0.8},
        }
    )
    teams, score, expected = verifier.raw_expectations(raw, match)
    assert teams == ("1", "2") and score == {"1": 1, "2": 1}
    assert expected["1", "drawing"]["shots.total"]["value"] == 2
    assert expected["1", "drawing"]["shots.goals"]["value"] == 1
    assert expected["2", "trailing"]["shots.goals"]["value"] == 1
    assert expected["1", "leading"]["passes.attempted"]["value"] == 1


def test_independent_oracle_counts_paired_own_goal_once(tmp_path):
    _, match, _ = fixture(tmp_path)
    match = match | {"home_score": 0, "away_score": 1}
    raw = [
        {
            "id": "against",
            "index": 1,
            "period": 1,
            "team": {"id": 1},
            "type": {"name": "Own Goal Against"},
        },
        {"id": "for", "index": 2, "period": 1, "team": {"id": 2}, "type": {"name": "Own Goal For"}},
        {"id": "pass", "index": 3, "period": 1, "team": {"id": 1}, "type": {"name": "Pass"}},
    ]
    _, score, expected = verifier.raw_expectations(raw, match)
    assert score == {"1": 0, "2": 1}
    assert expected["1", "trailing"]["passes.attempted"]["value"] == 1


def test_missing_xg_has_independent_partial_status_and_coverage(tmp_path):
    path, match, _ = fixture(tmp_path)
    raw = json.loads(path.read_text())
    shot = next(e for e in raw if e["type"]["name"] == "Shot")
    del shot["shot"]["statsbomb_xg"]
    _, _, expected = verifier.raw_expectations(raw, match)
    entry = expected["1", "drawing"]["shots.xg"]
    assert entry["status"] == "incomplete" and entry["value"] is None
    assert entry["eligible_events"] == 2 and entry["covered_events"] == 1


def test_reference_score_mismatch_and_duplicates_are_rejected(tmp_path):
    path, match, _ = fixture(tmp_path)
    raw = json.loads(path.read_text())
    with pytest.raises(ValueError, match="score differs"):
        verifier.raw_expectations(raw, match | {"home_score": 99})
    with pytest.raises(ValueError, match="Duplicate"):
        verifier.raw_expectations([*raw, raw[0]], match)


def test_batch_uses_read_only_snapshot_and_savepoint_per_match(tmp_path):
    engine = MagicMock()
    connection = engine.connect.return_value.execution_options.return_value.__enter__.return_value
    matches = [{"match_id": 1}, {"match_id": 2}]
    with patch.object(
        verifier,
        "verify_match",
        side_effect=[ValueError("bad source"), {"match_id": 2, "comparisons": 42}],
    ):
        report = verifier.verify_batch(engine, matches, tmp_path)
    engine.connect.return_value.execution_options.assert_called_once_with(
        isolation_level="REPEATABLE READ"
    )
    assert str(connection.execute.call_args.args[0]) == "SET TRANSACTION READ ONLY"
    assert connection.begin_nested.call_count == 2
    assert report["verified"] == [{"match_id": 2, "comparisons": 42}]
    assert report["failures"] == [
        {"match_id": 1, "error_type": "ValueError", "error": "bad source"}
    ]
    assert report["read_only"] is True


@pytest.mark.parametrize("matches", [[], [{"match_id": 1}, {"match_id": 1}]])
def test_empty_or_duplicate_match_selection_never_connects(matches, tmp_path):
    engine = Mock()
    with pytest.raises(ValueError):
        verifier.verify_batch(engine, matches, tmp_path)
    engine.connect.assert_not_called()
