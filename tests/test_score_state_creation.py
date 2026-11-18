import json
from unittest.mock import Mock, patch

import pytest
from test_score_state_execution import data, execute, values

from football_analytics.analytics.score_state_execution import (
    ScoreStateQuery,
    ScoreStateResult,
    prepare_score_state,
    score_state_value,
)

METRICS = tuple(
    "creation." + name
    for name in (
        "pass_count",
        "candidate_pairs",
        "usable_pairs",
        "review_pairs",
        "recorded_shot_assists",
        "recorded_goal_assists",
        "passes_with_any_recorded_flag",
        "passes_with_both_recorded_flags",
        "usable_pairs_without_positive_flag",
    )
)


def query(**changes):
    return ScoreStateQuery(
        **(
            {
                "match_ids": (123,),
                "group_by": "team",
                "score_state": "drawing",
                "score_state_contract_version": "1.1.0",
                "metrics": METRICS,
            }
            | changes
        )
    )


def linked_source(*, pass_details=None, shot_details=None, period=1):
    return data(
        extra=[
            (
                "Pass",
                1,
                1,
                {"pass": {"assisted_shot_id": "e4", "shot_assist": True, **(pass_details or {})}},
            ),
            (
                "Shot",
                1,
                period,
                {
                    "player": {"id": 102},
                    "shot": {
                        "outcome": {"name": "Saved"},
                        "type": {"name": "Open Play"},
                        "statsbomb_xg": 0.2,
                        "key_pass_id": "e3",
                        **(shot_details or {}),
                    },
                },
            ),
        ]
    )


def test_goal_assist_and_shot_assist_are_not_double_counted():
    source = linked_source(
        pass_details={"goal_assist": True}, shot_details={"outcome": {"name": "Goal"}}
    )
    result = execute(query(), source)
    row = values(result)
    assert row["creation.usable_pairs"].value == 1
    assert row["creation.recorded_shot_assists"].value == 1
    assert row["creation.recorded_goal_assists"].value == 1
    assert row["creation.passes_with_any_recorded_flag"].value == 1
    assert row["creation.passes_with_both_recorded_flags"].value == 1
    assert result.score_state_contract_version == "1.1.0"


def test_pass_score_is_used_even_when_shot_occurs_in_different_score_state():
    source = data(
        extra=[
            ("Pass", 1, 1, {"pass": {"assisted_shot_id": "e5", "shot_assist": True}}),
            (
                "Shot",
                2,
                1,
                {
                    "shot": {
                        "outcome": {"name": "Goal"},
                        "type": {"name": "Open Play"},
                        "statsbomb_xg": 0.3,
                    }
                },
            ),
            (
                "Shot",
                1,
                1,
                {
                    "player": {"id": 102},
                    "shot": {
                        "outcome": {"name": "Saved"},
                        "type": {"name": "Open Play"},
                        "key_pass_id": "e3",
                        "statsbomb_xg": 0.2,
                    },
                },
            ),
        ]
    )
    drawing = execute(query(metrics=("creation.usable_pairs", "shots.total")), source)
    trailing = execute(
        query(score_state="trailing", metrics=("creation.usable_pairs", "shots.total")), source
    )
    assert values(drawing)["creation.usable_pairs"].value == 1
    assert values(drawing)["shots.total"].value == 0
    assert values(trailing)["creation.usable_pairs"].value == 0
    assert values(trailing)["shots.total"].value == 1
    scope = next(s for s in drawing.score_evidence[0].creation_scope if s.team_id == "1")
    assert scope.pairs[0].external_shot_event_id == "e5"
    assert scope.summary.candidate_shot_event_ids == ("e5",)


def test_reverse_only_link_remains_usable_without_positive_flags():
    source = data(
        extra=[
            ("Pass", 1, 1, {}),
            (
                "Shot",
                1,
                1,
                {
                    "player": {"id": 102},
                    "shot": {
                        "outcome": {"name": "Saved"},
                        "type": {"name": "Open Play"},
                        "key_pass_id": "e3",
                    },
                },
            ),
        ]
    )
    row = values(execute(query(), source))
    assert row["creation.usable_pairs"].value == 1
    assert row["creation.usable_pairs_without_positive_flag"].value == 1
    assert row["creation.recorded_shot_assists"].value == 0


def test_cross_period_pair_is_retained_as_review_and_scoped_by_pass_period():
    source = linked_source(period=2)
    first = execute(query(periods=(1,)), source)
    second = execute(query(periods=(2,)), source)
    assert values(first)["creation.candidate_pairs"].value == 1
    assert values(first)["creation.review_pairs"].value == 1
    assert values(first)["creation.usable_pairs"].value == 0
    assert values(second)["creation.pass_count"].value == 0
    scope = next(s for s in first.score_evidence[0].creation_scope if s.team_id == "1")
    assert scope.pairs[0].shot_period == 2
    assert "period_mismatch" in scope.pairs[0].issues


def test_positive_flag_without_link_stays_diagnostic_not_an_assist():
    source = data(
        extra=[("Pass", 1, 1, {"pass": {"shot_assist": True, "assisted_shot_id": "missing"}})]
    )
    result = execute(query(), source)
    assert values(result)["creation.recorded_shot_assists"].value == 0
    assert (
        "creation_positive_flags_without_link"
        in values(result)["creation.usable_pairs"].review_reasons
    )
    assert (
        "creation_reference_issues_in_match"
        in values(result)["creation.usable_pairs"].review_reasons
    )
    scope = next(s for s in result.score_evidence[0].creation_scope if s.team_id == "1")
    assert scope.summary.flagged_unlinked_pass_event_ids == ("e3",)
    assert scope.match_reference_issues[0].external_target_event_id == "missing"


def test_ambiguous_pass_with_two_shots_preserves_both_review_pairs():
    source = data(
        extra=[
            ("Pass", 1, 1, {"pass": {"assisted_shot_id": "e4", "shot_assist": True}}),
            (
                "Shot",
                1,
                1,
                {
                    "player": {"id": 102},
                    "shot": {
                        "outcome": {"name": "Saved"},
                        "type": {"name": "Open Play"},
                        "key_pass_id": "e3",
                    },
                },
            ),
            (
                "Shot",
                1,
                1,
                {
                    "player": {"id": 103},
                    "shot": {
                        "outcome": {"name": "Saved"},
                        "type": {"name": "Open Play"},
                        "key_pass_id": "e3",
                    },
                },
            ),
        ]
    )
    row = values(execute(query(), source))
    assert row["creation.pass_count"].value == 1
    assert row["creation.candidate_pairs"].value == 2
    assert row["creation.review_pairs"].value == 2
    assert row["creation.usable_pairs"].value == 0
    assert "creation_pairs_require_review" in row["creation.usable_pairs"].review_reasons


def test_penalty_link_does_not_become_usable_creation():
    result = execute(query(), linked_source(shot_details={"type": {"name": "Penalty"}}))
    assert values(result)["creation.usable_pairs"].value == 0
    assert values(result)["creation.review_pairs"].value == 1


def test_unresolved_score_keeps_creation_unknown():
    source = data(extra=[("Own Goal Against", 1, 1, {}), ("Pass", 1, 1, {})])
    result = execute(query(), source)
    assert all(v.status == "unknown" for row in result.rows for v in row.values)
    assert result.score_evidence[0].creation_scope == ()


def test_empty_team_creation_is_zero_with_explicit_empty_evidence():
    result = execute(query(team_ids=("2",)), linked_source())
    assert all(v.value == 0 for v in result.rows[0].values)
    scope = next(s for s in result.score_evidence[0].creation_scope if s.team_id == "2")
    assert scope.summary.pass_event_ids == () and scope.pairs == ()


def test_creation_requires_explicit_extension_version():
    with pytest.raises(ValueError, match="1.1.0"):
        query(score_state_contract_version="1.0.0")


def test_json_round_trip_preserves_selected_pass_ids_and_global_link_issues():
    result = execute(query(), linked_source())
    restored = ScoreStateResult.model_validate_json(result.model_dump_json())
    assert restored == result
    scope = next(s for s in restored.score_evidence[0].creation_scope if s.team_id == "1")
    assert scope.scope_basis == "pass_period_and_pre_action_score"
    assert scope.summary.pass_event_ids == ("e3",)
    assert scope.summary.usable_pass_event_ids == ("e3",)
    assert scope.pairs[0].external_shot_event_id == "e4"


def test_result_without_creation_scope_cannot_claim_resolved_creation_values():
    result = execute(query(), linked_source())
    raw = result.model_dump(mode="json")
    raw["score_evidence"][0]["creation_scope"] = []
    with pytest.raises(ValueError, match="both source teams"):
        ScoreStateResult.model_validate_json(json.dumps(raw))


def test_mismatched_creation_pair_counts_are_rejected_on_read():
    result = execute(query(), linked_source())
    raw = result.model_dump(mode="json")
    scope = next(s for s in raw["score_evidence"][0]["creation_scope"] if s["team_id"] == "1")
    scope["summary"]["counts"]["usable_pairs"] = 99
    with pytest.raises(ValueError, match="pair counts"):
        ScoreStateResult.model_validate_json(json.dumps(raw))


def test_scalar_context_cannot_silently_return_zero_for_unprepared_creation():
    source = linked_source()
    context = prepare_score_state(
        source[1],
        query=query(metrics=("shots.total",)),
        match_id=123,
        team_ids=("1", "2"),
        events_sha256=source[0].events_sha256,
    )
    with pytest.raises(ValueError, match="not prepared"):
        score_state_value("creation.usable_pairs", context, "1")


def test_selected_matches_sum_creation_counts_and_keep_per_match_evidence():
    source = linked_source()
    q = query(match_ids=(123, 456), aggregation="selected_matches")

    def sources(_connection, match_id):
        ref, events, lineups = source
        return ref.model_copy(update={"match_id": match_id}), events, lineups

    from football_analytics.analytics.executor import execute_query

    with (
        patch("football_analytics.analytics.executor._sources", side_effect=sources),
        patch("football_analytics.analytics.score_state_execution.verify_stored_participation"),
    ):
        result = execute_query(Mock(), q)
    assert values(result)["creation.usable_pairs"].value == 2
    assert len(result.score_evidence) == 2


def verifier_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_score_state_creation.py"
    spec = importlib.util.spec_from_file_location("creation_scope_verifier", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verifier_local_mode_checks_54_creation_values_and_preserves_evidence(tmp_path):
    module = verifier_module()
    source = linked_source()
    path = tmp_path / "123.json"
    path.write_text(json.dumps(source[1]))
    match = {
        "match_id": 123,
        "home_team": {"home_team_id": 1},
        "away_team": {"away_team_id": 2},
        "home_score": 0,
        "away_score": 0,
    }
    result = module.verify_match(path, match)
    assert result["comparisons"] == 54
    assert len(result["creation_evidence"]) == 3
    assert result["creation_evidence"][0]["database_sources"] == []


def test_verifier_database_mode_uses_executor_results_and_stores_sources(tmp_path):
    module = verifier_module()
    source = linked_source()
    path = tmp_path / "123.json"
    path.write_text(json.dumps(source[1]))
    match = {
        "match_id": 123,
        "home_team": {"home_team_id": 1},
        "away_team": {"away_team_id": 2},
        "home_score": 0,
        "away_score": 0,
    }
    with patch.object(
        module, "execute_query", side_effect=lambda connection, q: execute(q, source)
    ) as calls:
        result = module.verify_match(path, match, Mock())
    assert calls.call_count == 3
    assert result["comparisons"] == 54
    assert (
        result["creation_evidence"][0]["database_sources"][0]["events_sha256"]
        == source[0].events_sha256
    )


def test_verifier_detects_a_pass_assigned_to_wrong_score_state(tmp_path):
    module = verifier_module()
    source = linked_source()
    path = tmp_path / "123.json"
    path.write_text(json.dumps(source[1]))
    match = {
        "match_id": 123,
        "home_team": {"home_team_id": 1},
        "away_team": {"away_team_id": 2},
        "home_score": 0,
        "away_score": 0,
    }

    def wrong(connection, q):
        result = execute(q, source)
        entry = result.score_evidence[0]
        scope = next(s for s in entry.creation_scope if s.team_id == "1")
        summary = scope.summary.model_copy(update={"pass_event_ids": ("wrong-pass",)})
        scope = scope.model_copy(update={"summary": summary})
        return result.model_copy(
            update={
                "score_evidence": (
                    entry.model_copy(update={"creation_scope": (scope, entry.creation_scope[1])}),
                )
            }
        )

    with patch.object(module, "execute_query", side_effect=wrong):
        with pytest.raises(ValueError):
            module.verify_match(path, match, Mock())
