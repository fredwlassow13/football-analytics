import hashlib
import json
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from test_analytics_executor import match_data

from football_analytics.analytics.contracts import MetricQuery
from football_analytics.analytics.executor import combine_values, execute_query
from football_analytics.analytics.score_state_execution import (
    ScoreStateQuery,
    ScoreStateResult,
    prepare_score_state,
    score_state_value,
)


def query(**updates):
    return ScoreStateQuery(
        **(
            {
                "match_ids": (123,),
                "group_by": "team",
                "metrics": ("passes.attempted", "shots.total", "shots.goals", "shots.xg"),
                "score_state": "drawing",
            }
            | updates
        )
    )


def data(extra=None):
    ref, source, lineups = match_data()
    events = source[:2]
    specs = (
        extra
        if extra is not None
        else [
            ("Pass", 1, 1, {}),
            (
                "Shot",
                1,
                1,
                {
                    "shot": {
                        "outcome": {"name": "Goal"},
                        "type": {"name": "Open Play"},
                        "statsbomb_xg": 0.3,
                    }
                },
            ),
            ("Pass", 1, 1, {}),
            (
                "Shot",
                2,
                1,
                {
                    "shot": {
                        "outcome": {"name": "Goal"},
                        "type": {"name": "Open Play"},
                        "statsbomb_xg": 0.4,
                    }
                },
            ),
            (
                "Shot",
                1,
                2,
                {
                    "shot": {
                        "outcome": {"name": "Saved"},
                        "type": {"name": "Open Play"},
                        "statsbomb_xg": 0.2,
                    }
                },
            ),
        ]
    )
    for i, (kind, team, period, changes) in enumerate(specs, start=3):
        item = {
            "id": f"e{i}",
            "index": i,
            "period": period,
            "timestamp": f"00:00:{i:02d}.000",
            "type": {"name": kind},
            "team": {"id": team},
            "player": {"id": team * 100 + 1},
        }
        if kind == "Pass":
            item["pass"] = {}
        events.append(item | changes)
    # Chronology matters; rearrange periods then regenerate unique indices.
    events.extend(
        [
            {
                "id": f"end{p}",
                "period": p,
                "timestamp": "00:45:00.000",
                "type": {"name": "Half End"},
            }
            for p in (1, 2)
        ]
    )
    events.sort(key=lambda e: (e["period"], e["timestamp"]))
    for index, item in enumerate(events, start=1):
        item["index"] = index
    ref = ref.model_copy(
        update={"events_sha256": hashlib.sha256(json.dumps(events).encode()).hexdigest()}
    )
    return ref, events, lineups


def prepare(q=None, source=None):
    ref, events, _ = source or data()
    return prepare_score_state(
        events,
        query=q or query(),
        match_id=123,
        team_ids=("1", "2"),
        events_sha256=ref.events_sha256,
    )


def execute(q=None, source=None):
    with (
        patch("football_analytics.analytics.executor._sources", return_value=source or data()),
        patch("football_analytics.analytics.score_state_execution.verify_stored_participation"),
    ):
        return execute_query(Mock(), q or query())


def values(result, team="1"):
    row = next(r for r in result.rows if r.team_id == team)
    return {v.metric_id: v for v in row.values}


def test_goal_is_counted_in_pre_goal_state_and_next_pass_in_leading():
    drawing = values(execute())
    leading = values(execute(query(score_state="leading")))
    assert drawing["passes.attempted"].value == 1
    assert drawing["shots.goals"].value == 1
    assert drawing["shots.total"].value == 2
    assert drawing["shots.xg"].value == Decimal(".5")
    assert leading["passes.attempted"].value == 1
    assert leading["shots.goals"].value == 0
    assert values(execute(query(score_state="trailing")), "2")["shots.goals"].value == 1


def test_period_filter_does_not_reset_score_reconstruction():
    source = data(
        extra=[
            (
                "Shot",
                1,
                1,
                {
                    "shot": {
                        "outcome": {"name": "Goal"},
                        "type": {"name": "Open Play"},
                        "statsbomb_xg": 0.3,
                    }
                },
            ),
            ("Pass", 1, 2, {}),
        ]
    )
    result = execute(query(periods=(2,), score_state="leading"), source)
    assert values(result)["passes.attempted"].value == 1
    assert result.score_evidence[0].final_score == (1, 0)


def test_own_goal_pair_changes_state_once():
    source = data(
        extra=[
            ("Own Goal Against", 1, 1, {"timestamp": "00:00:03.000", "related_events": ["e4"]}),
            ("Own Goal For", 2, 1, {"timestamp": "00:00:03.000", "related_events": ["e3"]}),
            ("Pass", 1, 1, {}),
        ]
    )
    result = execute(query(score_state="trailing"), source)
    assert values(result)["passes.attempted"].value == 1
    assert result.score_evidence[0].final_score == (0, 1)
    assert values(result)["shots.goals"].value == 0


def test_unpaired_own_goal_keeps_all_filtered_values_unknown():
    source = data(extra=[("Own Goal Against", 1, 1, {}), ("Pass", 1, 1, {})])
    result = execute(source=source)
    assert result.score_evidence[0].goal_status == "review"
    assert result.score_evidence[0].final_score is None
    assert all(v.status == "unknown" and v.value is None for r in result.rows for v in r.values)
    assert values(result)["passes.attempted"].reasons == ("unresolved_score_state",)


def test_missing_xg_preserves_partial_sum_and_null_complete_total():
    source = data(
        extra=[
            ("Shot", 1, 1, {"shot": {"outcome": {"name": "Saved"}, "statsbomb_xg": 0.2}}),
            ("Shot", 1, 1, {"shot": {"outcome": {"name": "Saved"}}}),
        ]
    )
    result = execute(query(metrics=("shots.xg", "shots.xg_available", "shots.missing_xg")), source)
    row = values(result)
    assert row["shots.xg"].status == "incomplete" and row["shots.xg"].value is None
    assert row["shots.xg_available"].value == Decimal(".2")
    assert row["shots.missing_xg"].value == 1


def test_state_without_actions_has_zero_counts_but_undefined_pass_percentage():
    result = execute(
        query(
            score_state="leading",
            team_ids=("2",),
            metrics=("passes.attempted", "passes.completion_pct", "shots.xg"),
        )
    )
    row = values(result, "2")
    assert row["passes.attempted"].value == 0
    assert row["shots.xg"].value == 0
    assert row["passes.completion_pct"].status == "not_applicable"


def test_query_and_result_json_preserve_score_filter_and_policy():
    result = execute()
    restored = ScoreStateResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.query.score_state == "drawing"
    assert restored.score_evidence[0].events_sha256 == restored.sources[0].events_sha256
    assert restored.score_evidence[0].policy_version == "1.0.0"


def test_ordinary_query_does_not_silently_accept_score_state_json():
    with pytest.raises(ValueError):
        MetricQuery.model_validate_json(query().model_dump_json())


@pytest.mark.parametrize(
    "updates",
    [
        {"group_by": "player"},
        {"metrics": ("participation.elapsed_on_field_ms",)},
        {"metrics": ("creation.usable_pairs",)},
        {"score_state": "unknown"},
        {"score_state": "invalid"},
    ],
)
def test_unsupported_scope_is_rejected(updates):
    with pytest.raises(ValueError):
        query(**updates)


def test_invalid_model_copy_cannot_bypass_dispatch_validation():
    with patch("football_analytics.analytics.executor._sources") as sources:
        with pytest.raises(ValueError):
            execute_query(Mock(), query().model_copy(update={"group_by": "player"}))
        sources.assert_not_called()


def test_team_filter_and_absent_team_validation():
    assert [r.team_id for r in execute(query(team_ids=("1",))).rows] == ["1"]
    with pytest.raises(ValueError, match="no rows"):
        execute(query(team_ids=("missing",)))


def test_unavailable_period_is_not_ignored():
    with pytest.raises(ValueError, match="unavailable"):
        execute(query(periods=(3,)))


def test_shootout_goal_does_not_affect_state_or_counts():
    source = data(
        extra=[
            ("Shot", 1, 5, {"shot": {"outcome": {"name": "Goal"}, "statsbomb_xg": 0.8}}),
            ("Pass", 1, 2, {}),
        ]
    )
    result = execute(source=source)
    assert values(result)["shots.total"].value == 0
    assert values(result)["passes.attempted"].value == 1
    assert result.score_evidence[0].final_score == (0, 0)


def test_weighted_aggregation_reuses_existing_ratio_policy():
    context = prepare(query(metrics=("passes.completion_pct",)))
    value = score_state_value("passes.completion_pct", context, "1")
    combined = combine_values([value, value])
    assert combined.value == 100 and combined.numerator == 2 and combined.denominator == 2


def test_source_hash_alignment_is_validated_on_result_read():
    result = execute()
    document = result.model_dump(mode="json")
    document["score_evidence"][0]["events_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="hash"):
        ScoreStateResult.model_validate_json(json.dumps(document))


def test_unresolved_evidence_cannot_be_published_as_available_result():
    result = execute()
    document = result.model_dump(mode="json")
    document["score_evidence"][0].update(goal_status="review", final_score=None)
    with pytest.raises(ValueError, match="unknown"):
        ScoreStateResult.model_validate_json(json.dumps(document))


def test_selected_matches_aggregates_filtered_counts_through_executor():
    source = data()
    q = query(match_ids=(123, 456), aggregation="selected_matches")

    def sources(_connection, match_id):
        ref, events, lineups = source
        return ref.model_copy(update={"match_id": match_id}), events, lineups

    with (
        patch("football_analytics.analytics.executor._sources", side_effect=sources),
        patch("football_analytics.analytics.score_state_execution.verify_stored_participation"),
    ):
        result = execute_query(Mock(), q)
    assert len(result.score_evidence) == 2
    assert all(row.match_ids == (123, 456) for row in result.rows)
    assert values(result)["passes.attempted"].value == 2
    assert values(result)["shots.xg"].value == Decimal("1.0")


def test_unknown_score_in_one_match_propagates_to_selected_matches():
    known = data()
    unknown = data(extra=[("Own Goal Against", 1, 1, {}), ("Pass", 1, 1, {})])
    q = query(match_ids=(123, 456), aggregation="selected_matches")

    def sources(_connection, match_id):
        ref, events, lineups = known if match_id == 123 else unknown
        return ref.model_copy(update={"match_id": match_id}), events, lineups

    with (
        patch("football_analytics.analytics.executor._sources", side_effect=sources),
        patch("football_analytics.analytics.score_state_execution.verify_stored_participation"),
    ):
        result = execute_query(Mock(), q)
    assert all(v.status == "unknown" for row in result.rows for v in row.values)


def test_dribble_counts_and_percentage_use_filtered_actions():
    source = data(
        extra=[
            ("Dribble", 1, 1, {"dribble": {"outcome": {"name": "Complete"}}}),
            ("Shot", 1, 1, {"shot": {"outcome": {"name": "Goal"}, "statsbomb_xg": 0.3}}),
            ("Dribble", 1, 1, {"dribble": {"outcome": {"name": "Incomplete"}}}),
        ]
    )
    metrics = ("dribbles.attempted", "dribbles.completed", "dribbles.completion_pct")
    drawing = values(execute(query(metrics=metrics), source))
    leading = values(execute(query(metrics=metrics, score_state="leading"), source))
    assert drawing["dribbles.attempted"].value == 1
    assert drawing["dribbles.completion_pct"].value == 100
    assert leading["dribbles.attempted"].value == 1
    assert leading["dribbles.completion_pct"].value == 0
