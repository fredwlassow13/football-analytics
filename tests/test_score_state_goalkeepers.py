import hashlib
import json

import pytest
from test_score_state_execution import data, execute, values

from football_analytics.analytics.score_state_execution import (
    ScoreStateQuery,
    ScoreStateResult,
    prepare_score_state,
    score_state_value,
)

METRICS = tuple(
    f"goalkeepers.{scope}.{member}"
    for scope in ("counts", "penalty_counts", "non_penalty_counts", "unknown_penalty_counts")
    for member in (
        "pair_count",
        "shot_goals_conceded",
        "on_target_saves",
        "off_target_saves",
        "other_shots",
        "review_pairs",
    )
)


def query(**changes):
    return ScoreStateQuery(
        **(
            {
                "match_ids": (123,),
                "group_by": "team",
                "metrics": METRICS,
                "score_state": "drawing",
                "score_state_contract_version": "1.3.0",
            }
            | changes
        )
    )


def source(extra=None, *, known_roles=True):
    if extra is None:
        extra = [
            (
                "Shot",
                1,
                1,
                {
                    "related_events": ["e4"],
                    "player": {"id": 102},
                    "shot": {
                        "outcome": {"name": "Saved"},
                        "type": {"name": "Open Play"},
                        "statsbomb_xg": 0.2,
                    },
                },
            ),
            (
                "Goal Keeper",
                2,
                1,
                {
                    "related_events": ["e3"],
                    "player": {"id": 201},
                    "goalkeeper": {"type": {"name": "Shot Saved"}, "outcome": {"name": "Success"}},
                },
            ),
        ]
    ref, events, lineups = data(extra=extra)
    for event in events:
        if event["type"]["name"] == "Starting XI":
            event["tactics"]["formation"] = 433
            for i, p in enumerate(event["tactics"]["lineup"]):
                p["position"] = {
                    "id": 1 if known_roles and i == 0 else 2,
                    "name": "Goalkeeper" if known_roles and i == 0 else "Right Back",
                }
    return (
        ref.model_copy(
            update={"events_sha256": hashlib.sha256(json.dumps(events).encode()).hexdigest()}
        ),
        events,
        lineups,
    )


def scope(result, team="2"):
    return next(s for s in result.score_evidence[0].goalkeeper_scope if s.team_id == team)


def pair_source(
    outcome="Goal", kind="Goal Conceded", shot_type="Open Play", *, keeper_period=1, shot_period=1
):
    shot = {"outcome": {"name": outcome}, "statsbomb_xg": 0.3}
    if shot_type is not None:
        shot["type"] = {"name": shot_type}
    return source(
        [
            (
                "Shot",
                1,
                shot_period,
                {"related_events": ["e4"], "player": {"id": 102}, "shot": shot},
            ),
            (
                "Goal Keeper",
                2,
                keeper_period,
                {
                    "related_events": ["e3"],
                    "player": {"id": 201},
                    "goalkeeper": {"type": {"name": kind}},
                },
            ),
        ]
    )


def test_conceded_goal_uses_defending_score_before_shot_not_after_goal():
    result = execute(query(), pair_source())
    assert values(result, "2")["goalkeepers.counts.shot_goals_conceded"].value == 1
    trailing = execute(query(score_state="trailing"), pair_source())
    assert values(trailing, "2")["goalkeepers.counts.shot_goals_conceded"].value == 0
    assert scope(result).pairs[0].external_shot_event_id == "e3"
    assert scope(result).scope_basis == "shot_period_and_defending_team_pre_shot_score"


def test_intervening_goal_does_not_move_save_into_keeper_event_score_state():
    data = source(
        [
            (
                "Shot",
                1,
                1,
                {
                    "player": {"id": 102},
                    "shot": {"outcome": {"name": "Saved"}, "type": {"name": "Open Play"}},
                },
            ),
            (
                "Shot",
                1,
                1,
                {
                    "player": {"id": 103},
                    "shot": {"outcome": {"name": "Goal"}, "type": {"name": "Open Play"}},
                },
            ),
            (
                "Goal Keeper",
                2,
                1,
                {
                    "related_events": ["e3"],
                    "player": {"id": 201},
                    "goalkeeper": {"type": {"name": "Shot Saved"}, "outcome": {"name": "Success"}},
                },
            ),
        ]
    )
    drawing = execute(query(), data)
    trailing = execute(query(score_state="trailing"), data)
    assert values(drawing, "2")["goalkeepers.counts.on_target_saves"].value == 1
    assert values(trailing, "2")["goalkeepers.counts.on_target_saves"].value == 0
    assert scope(drawing).unlinked_opponent_shot_event_ids == ("e4",)


def test_saves_penalties_and_unknown_penalty_status_keep_separate_partitions():
    penalty = execute(query(), pair_source("Saved", "Penalty Saved", "Penalty"))
    assert values(penalty, "2")["goalkeepers.penalty_counts.on_target_saves"].value == 1
    assert values(penalty, "2")["goalkeepers.non_penalty_counts.on_target_saves"].value == 0
    unknown = execute(query(), pair_source("Saved", "Shot Saved", None))
    assert values(unknown, "2")["goalkeepers.unknown_penalty_counts.review_pairs"].value == 1
    assert values(unknown, "2")["goalkeepers.counts.on_target_saves"].value == 0


def test_off_target_save_is_not_an_on_target_save():
    result = execute(query(), pair_source("Saved Off Target", "Shot Saved Off T"))
    assert values(result, "2")["goalkeepers.counts.off_target_saves"].value == 1
    assert values(result, "2")["goalkeepers.counts.on_target_saves"].value == 0


def test_shot_faced_is_not_a_save():
    result = execute(query(), pair_source("Off T", "Shot Faced"))
    assert values(result, "2")["goalkeepers.counts.other_shots"].value == 1
    assert values(result, "2")["goalkeepers.counts.on_target_saves"].value == 0


def test_mismatched_keeper_action_remains_review():
    result = execute(query(), pair_source("Saved", "Shot Faced"))
    assert values(result, "2")["goalkeepers.counts.review_pairs"].value == 1
    assert values(result, "2")["goalkeepers.counts.on_target_saves"].value == 0
    assert (
        "goalkeeper_pairs_require_review"
        in values(result, "2")["goalkeepers.counts.pair_count"].review_reasons
    )


def test_reverse_only_relationship_is_resolved_before_scope_selection():
    ref, events, lineups = source()
    keeper = next(e for e in events if e["type"]["name"] == "Goal Keeper")
    del keeper["related_events"]
    result = execute(query(), (ref, events, lineups))
    assert values(result, "2")["goalkeepers.counts.on_target_saves"].value == 1


def test_two_keepers_for_one_shot_keep_both_ambiguous_pairs():
    data = source(
        [
            (
                "Shot",
                1,
                1,
                {
                    "player": {"id": 102},
                    "shot": {"outcome": {"name": "Saved"}, "type": {"name": "Open Play"}},
                },
            ),
            (
                "Goal Keeper",
                2,
                1,
                {
                    "related_events": ["e3"],
                    "player": {"id": 201},
                    "goalkeeper": {"type": {"name": "Shot Saved"}},
                },
            ),
            (
                "Goal Keeper",
                2,
                1,
                {
                    "related_events": ["e3"],
                    "player": {"id": 202},
                    "goalkeeper": {"type": {"name": "Shot Saved"}},
                },
            ),
        ]
    )
    result = execute(query(), data)
    assert values(result, "2")["goalkeepers.counts.pair_count"].value == 2
    assert values(result, "2")["goalkeepers.counts.review_pairs"].value == 2
    assert scope(result).candidate_shot_event_ids == ("e3",)


def test_cross_period_pair_is_review_and_selected_by_shot_period():
    data = pair_source("Saved", "Shot Saved", keeper_period=2, shot_period=1)
    first = execute(query(periods=(1,)), data)
    second = execute(query(periods=(2,)), data)
    assert values(first, "2")["goalkeepers.counts.review_pairs"].value == 1
    assert values(second, "2")["goalkeepers.counts.pair_count"].value == 0
    assert scope(first).pairs[0].goalkeeper_period == 2


def test_unlinked_keeper_actions_are_not_assigned_a_fake_shot_context():
    data = source(
        [
            (
                "Shot",
                1,
                1,
                {
                    "player": {"id": 102},
                    "shot": {"outcome": {"name": "Saved"}, "type": {"name": "Open Play"}},
                },
            ),
            (
                "Goal Keeper",
                2,
                1,
                {"player": {"id": 201}, "goalkeeper": {"type": {"name": "Shot Saved"}}},
            ),
            (
                "Goal Keeper",
                2,
                1,
                {"player": {"id": 201}, "goalkeeper": {"type": {"name": "Keeper Sweeper"}}},
            ),
        ]
    )
    result = execute(query(), data)
    assert values(result, "2")["goalkeepers.counts.pair_count"].value == 0
    assert scope(result).match_unlinked_goalkeeper_event_ids == ("e4", "e5")
    assert scope(result).match_unlinked_shot_action_ids == ("e4",)
    assert scope(result).unlinked_opponent_shot_event_ids == ("e3",)


def test_missing_relation_target_keeps_pair_review_and_match_diagnostic():
    ref, events, lineups = source()
    next(e for e in events if e["type"]["name"] == "Goal Keeper")["related_events"].append(
        "missing"
    )
    result = execute(query(), (ref, events, lineups))
    assert values(result, "2")["goalkeepers.counts.review_pairs"].value == 1
    assert scope(result).match_source_issues[0].external_target_event_id == "missing"


def test_unresolved_score_keeps_keeper_metrics_unknown():
    data = source([("Own Goal Against", 1, 1, {})])
    result = execute(query(), data)
    assert all(v.status == "unknown" for row in result.rows for v in row.values)
    assert result.score_evidence[0].goalkeeper_scope == ()


def test_missing_role_evidence_does_not_become_zero():
    result = execute(query(), source([], known_roles=False))
    assert all(v.status == "unknown" for row in result.rows for v in row.values)
    assert values(result, "2")["goalkeepers.counts.pair_count"].reasons == (
        "goalkeeper_role_unknown",
    )


def test_unprovided_population_in_pure_context_does_not_assume_keeper_presence():
    ref, events, lineups = source()
    context = prepare_score_state(
        events, query=query(), match_id=123, team_ids=("1", "2"), events_sha256=ref.events_sha256
    )
    assert score_state_value("goalkeepers.counts.pair_count", context, "2").status == "unknown"


def test_known_keeper_role_without_state_actions_has_zero_pair_counts():
    result = execute(query(score_state="leading"), source())
    assert all(v.value == 0 and v.status == "available" for row in result.rows for v in row.values)


def test_shootout_pairs_are_excluded_even_with_penalty_type():
    result = execute(
        query(), pair_source("Saved", "Penalty Saved", "Penalty", keeper_period=5, shot_period=5)
    )
    assert values(result, "2")["goalkeepers.counts.pair_count"].value == 0
    assert scope(result).match_excluded_shootout_pairs == (("e4", "e3"),)


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0"])
def test_keeper_queries_require_version_1_3(version):
    with pytest.raises(ValueError, match="1.3.0"):
        query(score_state_contract_version=version)


def test_json_roundtrip_keeps_pair_counts_and_coverage_evidence():
    result = execute(query(), source())
    assert ScoreStateResult.model_validate_json(result.model_dump_json()) == result


def test_invalid_count_partition_is_rejected_when_reading_result():
    result = execute(query(), source())
    raw = result.model_dump(mode="json")
    raw["score_evidence"][0]["goalkeeper_scope"][1]["counts"]["on_target_saves"] = 99
    with pytest.raises(ValueError, match="counts differ"):
        ScoreStateResult.model_validate_json(json.dumps(raw))


def test_new_version_can_mix_keeper_creation_and_defensive_metrics():
    result = execute(
        query(
            metrics=(
                "goalkeepers.counts.on_target_saves",
                "creation.pass_count",
                "defense.pressures",
            )
        ),
        source(),
    )
    assert values(result, "2")["goalkeepers.counts.on_target_saves"].value == 1
    assert len(result.score_evidence[0].creation_scope) == 2
    assert len(result.score_evidence[0].defensive_scope) == 2


def verifier_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_score_state_goalkeepers.py"
    spec = importlib.util.spec_from_file_location("keeper_scope_verifier", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verification_fixture(tmp_path):
    sample = pair_source()
    events_path = tmp_path / "events.json"
    lineups_path = tmp_path / "lineups.json"
    events_path.write_text(json.dumps(sample[1]))
    lineups_path.write_text(json.dumps(sample[2]))
    match = {
        "match_id": 123,
        "home_team": {"home_team_id": 1},
        "away_team": {"away_team_id": 2},
        "home_score": 1,
        "away_score": 0,
    }
    return sample, events_path, lineups_path, match


def test_verifier_checks_24_metrics_in_three_states_with_pair_evidence(tmp_path):
    module = verifier_module()
    sample, events_path, lineups_path, match = verification_fixture(tmp_path)
    result = module.verify_match(events_path, match, lineups_path)
    assert len(module.METRICS) == 24
    assert result["comparisons"] == 144
    assert len(result["goalkeeper_evidence"]) == 3


def test_database_verifier_checks_events_and_lineups_against_local_files(tmp_path):
    from unittest.mock import Mock, patch

    module = verifier_module()
    sample, events_path, lineups_path, match = verification_fixture(tmp_path)
    with patch.object(
        module, "execute_query", side_effect=lambda connection, q: execute(q, sample)
    ):
        result = module.verify_match(events_path, match, lineups_path, Mock())
    assert result["comparisons"] == 144
    assert result["lineups_sha256"] == sample[0].lineups_sha256


def test_reference_assigns_conceded_goal_to_drawing_for_defender(tmp_path):
    module = verifier_module()
    sample, events_path, lineups_path, match = verification_fixture(tmp_path)
    teams, score, expected = module.expected_scopes(
        sample[1],
        match,
        sample[0].events_sha256,
        {"1": "presence_confirmed", "2": "presence_confirmed"},
    )
    assert (
        expected["2", "drawing"]["values"]["goalkeepers.counts.shot_goals_conceded"]["value"] == 1
    )
    assert (
        expected["2", "trailing"]["values"]["goalkeepers.counts.shot_goals_conceded"]["value"] == 0
    )


def test_aggregate_keeps_unknown_role_from_applicable_match():
    from unittest.mock import Mock, patch

    from football_analytics.analytics.executor import execute_query

    known = source()
    unknown = source([], known_roles=False)
    q = query(match_ids=(123, 456), aggregation="selected_matches")

    def sources(connection, match_id):
        ref, events, lineups = known if match_id == 123 else unknown
        return ref.model_copy(update={"match_id": match_id}), events, lineups

    with (
        patch("football_analytics.analytics.executor._sources", side_effect=sources),
        patch("football_analytics.analytics.score_state_execution.verify_stored_participation"),
    ):
        result = execute_query(Mock(), q)
    assert all(value.status == "unknown" for row in result.rows for value in row.values)


def test_cross_shootout_pair_does_not_hide_missing_selected_opponent_pair():
    data = pair_source("Saved", "Penalty Saved", "Penalty", keeper_period=5, shot_period=1)
    result = execute(query(), data)
    assert values(result, "2")["goalkeepers.counts.pair_count"].value == 0
    assert scope(result).opponent_shots_without_selected_pair_ids == ("e3",)
    assert scope(result).match_excluded_shootout_pairs == (("e4", "e3"),)
    assert (
        "goalkeeper_opponent_shots_without_selected_pair"
        in values(result, "2")["goalkeepers.counts.pair_count"].review_reasons
    )
