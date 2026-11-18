import json
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from test_score_state_execution import data, execute, values

from football_analytics.analytics.score_state_execution import ScoreStateQuery, ScoreStateResult


def query(**changes):
    return ScoreStateQuery(
        **(
            {
                "match_ids": (123,),
                "group_by": "team",
                "score_state": "drawing",
                "score_state_contract_version": "1.2.0",
                "metrics": ("defense.unique_sources", "defense.pressures"),
            }
            | changes
        )
    )


def scope(result, team="1"):
    return next(s for s in result.score_evidence[0].defensive_scope if s.team_id == team)


def test_pressure_is_attributed_to_actor_team_not_opponent_possession():
    source = data(
        extra=[
            ("Shot", 1, 1, {"shot": {"outcome": {"name": "Goal"}, "statsbomb_xg": 0.3}}),
            ("Pressure", 2, 1, {"possession": 1, "possession_team": {"id": 1}}),
        ]
    )
    trailing = execute(query(score_state="trailing"), source)
    leading = execute(query(score_state="leading"), source)
    assert values(trailing, "2")["defense.pressures"].value == 1
    assert values(leading, "2")["defense.pressures"].value == 0
    assert scope(trailing, "2").unique_source_event_ids == ("e4",)


def test_period_filter_preserves_goals_from_previous_period():
    source = data(
        extra=[
            ("Shot", 1, 1, {"shot": {"outcome": {"name": "Goal"}, "statsbomb_xg": 0.3}}),
            ("Pressure", 1, 2, {}),
        ]
    )
    result = execute(query(score_state="leading", periods=(2,)), source)
    assert values(result)["defense.pressures"].value == 1


def test_flags_keep_explicit_true_false_and_unknown_separate():
    source = data(
        extra=[
            ("Ball Recovery", 1, 1, {"ball_recovery": {"recovery_failure": True}}),
            ("Ball Recovery", 1, 1, {"ball_recovery": {"recovery_failure": False}}),
            ("Ball Recovery", 1, 1, {}),
        ]
    )
    metrics = tuple(
        "defense.ball_recoveries.recovery_failure." + flag for flag in ("true", "false", "unknown")
    )
    row = values(execute(query(metrics=metrics), source))
    assert [row[m].value for m in metrics] == [1, 1, 1]
    assert all(v.coverage.eligible_events == 3 for v in row.values())


def test_clearance_aerial_projection_does_not_double_count_unique_source():
    source = data(extra=[("Clearance", 1, 1, {"clearance": {"aerial_won": True}})])
    result = execute(
        query(
            metrics=(
                "defense.unique_sources",
                "defense.clearances",
                "aerial.won",
                "aerial.contested",
            )
        ),
        source,
    )
    assert all(v.value == 1 for v in values(result).values())
    evidence = scope(result)
    assert (
        evidence.action_event_ids
        == evidence.aerial_event_ids
        == evidence.shared_event_ids
        == ("e3",)
    )
    assert evidence.unique_source_event_ids == ("e3",)


def test_aerial_win_from_pass_uses_pre_goal_score_and_is_preserved_without_action_view():
    source = data(
        extra=[
            ("Pass", 1, 1, {"pass": {"aerial_won": True}}),
            ("Shot", 1, 1, {"shot": {"outcome": {"name": "Goal"}, "statsbomb_xg": 0.3}}),
        ]
    )
    result = execute(query(metrics=("aerial.won", "defense.unique_sources")), source)
    assert values(result)["aerial.won"].value == 1
    assert scope(result).action_event_ids == ()
    assert scope(result).aerial_event_ids == ("e3",)


def test_aerial_lost_source_is_one_unique_source_in_both_views():
    source = data(extra=[("Duel", 1, 1, {"duel": {"type": {"name": "Aerial Lost"}}})])
    result = execute(
        query(metrics=("defense.unique_sources", "duels.aerial_lost", "aerial.lost")), source
    )
    assert all(v.value == 1 for v in values(result).values())
    assert scope(result).shared_event_ids == ("e3",)


def test_distinct_tackle_outcomes_are_not_collapsed_to_binary_success():
    source = data(
        extra=[
            ("Duel", 1, 1, {"duel": {"type": {"name": "Tackle"}, "outcome": {"name": outcome}}})
            for outcome in ("Won", "Success", "Success In Play")
        ]
    )
    metrics = (
        "duels.tackles.won",
        "duels.tackles.success",
        "duels.tackles.success_in_play",
        "duels.tackles.total",
        "duels.total_duel_events",
    )
    row = values(execute(query(metrics=metrics), source))
    assert [row[m].value for m in metrics] == [1, 1, 1, 3, 3]


def test_aerial_percentage_uses_wins_over_contested():
    source = data(
        extra=[
            ("Pass", 1, 1, {"pass": {"aerial_won": True}}),
            ("Duel", 1, 1, {"duel": {"type": {"name": "Aerial Lost"}}}),
            ("Duel", 1, 1, {"duel": {"type": {"name": "Aerial Lost"}}}),
        ]
    )
    value = values(execute(query(metrics=("aerial.win_pct",)), source))["aerial.win_pct"]
    assert value.value == Decimal("33.33")
    assert value.numerator == 1 and value.denominator == 3


def test_empty_scope_has_zero_counts_and_undefined_aerial_percentage():
    result = execute(
        query(team_ids=("2",), metrics=("defense.unique_sources", "aerial.win_pct")),
        data(extra=[("Pressure", 1, 1, {})]),
    )
    row = values(result, "2")
    assert row["defense.unique_sources"].value == 0
    assert row["aerial.win_pct"].status == "not_applicable"
    assert scope(result, "2").summary is None
    assert scope(result, "2").related_ids_by_source == {}


def test_related_events_outside_selected_union_are_preserved():
    source = data(extra=[("Pressure", 1, 1, {"related_events": ["e4"]}), ("Pass", 2, 1, {})])
    result = execute(query(), source)
    evidence = scope(result)
    assert evidence.related_ids_by_source == {"e3": ("e4",)}
    assert evidence.related_targets_outside_selected_union == ("e4",)
    assert evidence.missing_related_target_ids == ()
    assert (
        "defensive_related_targets_missing"
        not in values(result)["defense.pressures"].review_reasons
    )


def test_missing_relation_target_is_diagnostic_not_an_added_action():
    source = data(extra=[("Pressure", 1, 1, {"related_events": ["missing"]})])
    result = execute(query(), source)
    assert values(result)["defense.unique_sources"].value == 1
    assert scope(result).missing_related_target_ids == ("missing",)
    assert "defensive_related_targets_missing" in values(result)["defense.pressures"].review_reasons


def test_shootout_actions_do_not_enter_defensive_scope():
    result = execute(query(), data(extra=[("Pressure", 1, 5, {})]))
    assert values(result)["defense.pressures"].value == 0
    assert scope(result).unique_source_event_ids == ()


def test_unresolved_goal_keeps_all_defensive_metrics_unknown():
    source = data(extra=[("Own Goal Against", 1, 1, {}), ("Pressure", 1, 1, {})])
    result = execute(query(), source)
    assert all(v.status == "unknown" for r in result.rows for v in r.values)
    assert result.score_evidence[0].defensive_scope == ()


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0"])
def test_defense_requires_explicit_extension_version(version):
    with pytest.raises(ValueError, match="1.2.0"):
        query(score_state_contract_version=version)


def test_keeper_metrics_remain_unsupported():
    with pytest.raises(ValueError):
        query(metrics=("goalkeepers.counts.on_target_saves",))


def test_version_1_2_can_combine_creation_and_defense():
    result = execute(
        query(metrics=("creation.pass_count", "defense.pressures")),
        data(extra=[("Pass", 1, 1, {}), ("Pressure", 1, 1, {})]),
    )
    assert values(result)["creation.pass_count"].value == 1
    assert values(result)["defense.pressures"].value == 1
    assert len(result.score_evidence[0].creation_scope) == 2
    assert len(result.score_evidence[0].defensive_scope) == 2


def test_json_roundtrip_preserves_shared_source_and_relations():
    source = data(
        extra=[
            ("Clearance", 1, 1, {"clearance": {"aerial_won": True}, "related_events": ["missing"]})
        ]
    )
    result = execute(query(), source)
    assert ScoreStateResult.model_validate_json(result.model_dump_json()) == result


def test_inconsistent_source_union_is_rejected_on_read():
    result = execute(query(), data(extra=[("Pressure", 1, 1, {})]))
    raw = result.model_dump(mode="json")
    raw["score_evidence"][0]["defensive_scope"][0]["unique_source_event_ids"] = []
    with pytest.raises(ValueError, match="source union"):
        ScoreStateResult.model_validate_json(json.dumps(raw))


def test_resolved_defensive_result_requires_evidence_for_both_teams():
    result = execute(query(), data(extra=[("Pressure", 1, 1, {})]))
    raw = result.model_dump(mode="json")
    raw["score_evidence"][0]["defensive_scope"] = []
    with pytest.raises(ValueError, match="both source teams"):
        ScoreStateResult.model_validate_json(json.dumps(raw))


def test_aggregation_recomputes_aerial_ratio_instead_of_averaging_percentages():
    first = data(extra=[("Pass", 1, 1, {"pass": {"aerial_won": True}})])
    second = data(
        extra=[("Duel", 1, 1, {"duel": {"type": {"name": "Aerial Lost"}}}) for _ in range(3)]
    )
    q = query(
        match_ids=(123, 456),
        aggregation="selected_matches",
        metrics=("aerial.win_pct", "aerial.contested"),
    )

    def sources(connection, match_id):
        ref, events, lineups = first if match_id == 123 else second
        return ref.model_copy(update={"match_id": match_id}), events, lineups

    from football_analytics.analytics.executor import execute_query

    with (
        patch("football_analytics.analytics.executor._sources", side_effect=sources),
        patch("football_analytics.analytics.score_state_execution.verify_stored_participation"),
    ):
        result = execute_query(Mock(), q)
    assert values(result)["aerial.win_pct"].value == 25
    assert values(result)["aerial.contested"].value == 4


def test_defensive_preparer_rejects_different_payload_coverage():
    from football_analytics.analytics.score_state_defense import prepare_score_state_defense
    from football_analytics.analytics.score_state_execution import prepare_score_state

    source = data(extra=[("Pressure", 1, 1, {})])
    context = prepare_score_state(
        source[1],
        query=query(metrics=("shots.total",)),
        match_id=123,
        team_ids=("1", "2"),
        events_sha256=source[0].events_sha256,
    )
    changed = [e for e in source[1] if e["id"] != "e3"]
    with pytest.raises(ValueError, match="coverage differs"):
        prepare_score_state_defense(changed, context.evolution, state="drawing", periods=None)


def test_defensive_preparer_rejects_unresolved_score_instead_of_zeroing():
    from football_analytics.analytics.score_state_defense import prepare_score_state_defense
    from football_analytics.analytics.score_state_execution import prepare_score_state

    source = data(extra=[("Own Goal Against", 1, 1, {})])
    context = prepare_score_state(
        source[1],
        query=query(metrics=("shots.total",)),
        match_id=123,
        team_ids=("1", "2"),
        events_sha256=source[0].events_sha256,
    )
    with pytest.raises(ValueError, match="unresolved goals"):
        prepare_score_state_defense(source[1], context.evolution, state="drawing", periods=None)


def verifier_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_score_state_defense.py"
    spec = importlib.util.spec_from_file_location("score_defense_verifier", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verification_fixture(tmp_path):
    source = data(
        extra=[
            ("Pressure", 1, 1, {}),
            ("Clearance", 1, 1, {"clearance": {"aerial_won": True}}),
            ("Duel", 1, 1, {"duel": {"type": {"name": "Aerial Lost"}}}),
        ]
    )
    path = tmp_path / "123.json"
    path.write_text(json.dumps(source[1]))
    match = {
        "match_id": 123,
        "home_team": {"home_team_id": 1},
        "away_team": {"away_team_id": 2},
        "home_score": 0,
        "away_score": 0,
    }
    return path, match, source


def test_local_verifier_covers_every_defensive_metric_and_preserves_evidence(tmp_path):
    module = verifier_module()
    path, match, source = verification_fixture(tmp_path)
    result = module.verify_match(path, match)
    assert len(module.METRICS) == 84
    assert result["comparisons"] == 504
    assert len(result["defensive_evidence"]) == 3
    assert result["defensive_evidence"][0]["database_sources"] == []


def test_database_verifier_compares_executor_results_and_saves_provenance(tmp_path):
    module = verifier_module()
    path, match, source = verification_fixture(tmp_path)
    with patch.object(
        module, "execute_query", side_effect=lambda connection, q: execute(q, source)
    ) as calls:
        result = module.verify_match(path, match, Mock())
    assert calls.call_count == 3
    assert result["comparisons"] == 504
    assert (
        result["defensive_evidence"][0]["database_sources"][0]["events_sha256"]
        == source[0].events_sha256
    )


def test_verifier_detects_incorrect_defensive_value(tmp_path):
    module = verifier_module()
    path, match, source = verification_fixture(tmp_path)

    def wrong(connection, q):
        result = execute(q, source)
        row = result.rows[0]
        changed = row.values[0].model_copy(update={"value": 999})
        return result.model_copy(
            update={
                "rows": (
                    row.model_copy(update={"values": (changed, *row.values[1:])}),
                    *result.rows[1:],
                )
            }
        )

    with patch.object(module, "execute_query", side_effect=wrong):
        with pytest.raises(ValueError, match="Defensive mismatch"):
            module.verify_match(path, match, Mock())


def test_raw_flag_oracle_does_not_convert_absence_to_false(tmp_path):
    module = verifier_module()
    source = data(extra=[("Pressure", 1, 1, {}), ("Pressure", 1, 1, {"counterpress": False})])
    match = {
        "match_id": 123,
        "home_team": {"home_team_id": 1},
        "away_team": {"away_team_id": 2},
        "home_score": 0,
        "away_score": 0,
    }
    teams, score, expected = module.expected_scopes(source[1], match, source[0].events_sha256)
    row = expected["1", "drawing"]["values"]
    assert row["defense.pressures.counterpress.unknown"]["value"] == 1
    assert row["defense.pressures.counterpress.false"]["value"] == 1
    assert row["defense.pressures.counterpress.true"]["value"] == 0
