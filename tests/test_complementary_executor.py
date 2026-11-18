from decimal import Decimal

import pytest
from test_analytics_executor import execute, mapping, match_data, query

from football_analytics.analytics.catalogue import METRICS


def data():
    ref, events, lineups = match_data()
    events[2]["location"] = [20.0, 40.0]
    events[2]["pass"].update(end_location=[70.0, 40.0], assisted_shot_id="shot", shot_assist=True)
    events[3]["period"] = 1
    events[3]["player"]["id"] = 102
    events[3]["shot"]["key_pass_id"] = "pass"

    def add(index, kind, pid, **details):
        events.append(
            {
                "id": f"c{index}",
                "index": index,
                "period": 1,
                "timestamp": "00:03:00.000",
                "type": {"name": kind},
                "team": {"id": 1},
                "player": {"id": pid},
                **details,
            }
        )

    add(8, "Dribble", 106, dribble={"outcome": {"name": "Complete"}})
    add(9, "Dribble", 106, dribble={"outcome": {"name": "Incomplete"}})
    add(10, "Carry", 107, location=[80.0, 40.0], carry={"end_location": [110.0, 40.0]})
    add(
        11,
        "Pass",
        108,
        location=[20.0, 40.0],
        **{"pass": {"outcome": {"name": "Incomplete"}, "end_location": [80.0, 40.0]}},
    )
    add(
        12,
        "Pass",
        108,
        location=[20.0, 40.0],
        **{"pass": {"outcome": {"name": "Unknown"}, "end_location": [80.0, 40.0]}},
    )
    add(13, "Carry", 107, carry={"end_location": [110.0, 40.0]})
    add(14, "Ball Recovery", 109)
    add(15, "Miscontrol", 109)
    for event in events:
        kind = event["type"]["name"]
        if kind in {"Shot", "Dribble"}:
            event["location"] = [100.0, 40.0]
        if kind == "Shot":
            event["shot"]["end_location"] = [120.0, 40.0, 1.0]
        if event["id"] == "c13":
            event["location"] = [121.0, 40.0]
    return ref, events, lineups


def test_dribble_ratio_and_dribble_only_player_population():
    r = execute(
        query(
            player_ids=("106",),
            metrics=("dribbles.attempted", "dribbles.completion_pct", "passes.attempted"),
        ),
        data(),
    )
    assert [v.value for v in r.rows[0].values] == [2, Decimal("50.00"), 0]
    assert r.rows[0].values[0].coverage.population == "observed"


def test_creation_confirmed_link_and_unlinked_positive_flag():
    r = execute(
        query(
            player_ids=("101",),
            metrics=(
                "creation.usable_pairs",
                "creation.recorded_shot_assists",
                "creation.recorded_goal_assists",
            ),
        ),
        data(),
    )
    assert [v.value for v in r.rows[0].values] == [1, 1, 0]
    d = data()
    d[1][2]["pass"].pop("assisted_shot_id")
    d[1][3]["shot"].pop("key_pass_id")
    r = execute(query(player_ids=("101",), metrics=("creation.recorded_shot_assists",)), d)
    assert r.rows[0].values[0].value == 0
    assert "creation_positive_flags_without_link" in r.rows[0].values[0].review_reasons


def test_creation_cross_period_is_review_not_assist():
    d = data()
    d[1][3]["period"] = 2
    r = execute(
        query(
            player_ids=("101",),
            periods=(1,),
            metrics=("creation.review_pairs", "creation.recorded_shot_assists"),
        ),
        d,
    )
    assert [v.value for v in r.rows[0].values] == [1, 0]
    assert "creation_pairs_require_review" in r.rows[0].values[0].review_reasons
    r = execute(query(player_ids=("101",), periods=(2,), metrics=("creation.review_pairs",)), d)
    assert "creation_cross_scope_pairs_in_match" in r.rows[0].values[0].review_reasons


def test_progression_completion_and_unknown_geometry_are_distinct():
    r = execute(
        query(
            player_ids=("108",),
            metrics=(
                "movement.pass.geometrically_progressive.true",
                "movement.pass.countable_progression.true",
                "movement.pass.countable_progression.false",
                "movement.pass.countable_progression.unknown",
            ),
        ),
        data(),
    )
    assert [v.value for v in r.rows[0].values] == [2, 0, 1, 1]
    r = execute(
        query(
            player_ids=("107",),
            metrics=(
                "movement.carry.countable_progression.true",
                "movement.carry.countable_progression.unknown",
            ),
        ),
        data(),
    )
    assert [v.value for v in r.rows[0].values] == [1, 1]
    assert "movement_geometry_unavailable" in r.rows[0].values[0].review_reasons


def test_control_categories_do_not_infer_recovery_success():
    r = execute(
        query(
            player_ids=("109",),
            metrics=(
                "ball_control.recovery_failure_unknown",
                "ball_control.recovery_failure_false",
                "ball_control.miscontrol",
            ),
        ),
        data(),
    )
    assert [v.value for v in r.rows[0].values] == [1, 0, 1]


def test_all_complementary_definitions_and_population_states():
    metrics = tuple(
        m.id for m in METRICS if m.family in {"dribbles", "creation", "movement", "ball_control"}
    )
    r = execute(query(metrics=metrics), data())
    p = {row.player_id: mapping(row) for row in r.rows}
    assert all(v.status == "unknown" for v in p["112"].values())
    assert p["102"]["dribbles.attempted"].value == 0
    assert p["102"]["dribbles.completion_pct"].status == "not_applicable"
    r = execute(query(group_by="team", periods=(2,), metrics=metrics), data())
    assert all(v.value in (0, None) for row in r.rows for v in row.values)


def test_unlisted_control_action_is_rejected():
    d = data()
    d[1][-1]["player"]["id"] = 999
    with pytest.raises(ValueError, match="roster"):
        execute(query(metrics=("ball_control.miscontrol",)), d)


def test_failed_pass_with_unusable_geometry_is_not_classified_false():
    d = data()
    next(e for e in d[1] if e["id"] == "c11")["location"] = [121.0, 40.0]
    r = execute(
        query(
            player_ids=("108",),
            metrics=(
                "movement.pass.countable_progression.false",
                "movement.pass.countable_progression.unknown",
            ),
        ),
        d,
    )
    assert [v.value for v in r.rows[0].values] == [0, 2]


def test_missing_mandatory_coordinates_are_rejected_by_source_adapter():
    from football_analytics.integrations.base import ProviderUnavailable

    d = data()
    d[1][2].pop("location")
    with pytest.raises(ProviderUnavailable, match="Invalid spatial"):
        execute(query(metrics=("movement.pass.countable_progression.true",)), d)


def test_unlisted_carry_is_rejected_even_without_offensive_event():
    d = data()
    next(e for e in d[1] if e["id"] == "c10")["player"]["id"] = 999
    with pytest.raises(ValueError, match="roster"):
        execute(query(metrics=("movement.carry.countable_progression.true",)), d)


def test_mixed_query_covers_entire_numeric_catalogue():
    d = data()
    for event in d[1][:2]:
        event["tactics"]["formation"] = 442
        for n, player in enumerate(event["tactics"]["lineup"]):
            player["position"] = {
                "id": 1 if n == 0 else 2,
                "name": "Goalkeeper" if n == 0 else "Right Back",
            }
    d[1][3]["related_events"] = ["keeper"]
    d[1].append(
        {
            "id": "keeper",
            "index": 16,
            "period": 1,
            "timestamp": "00:02:00.000",
            "type": {"name": "Goal Keeper"},
            "team": {"id": 2},
            "player": {"id": 201},
            "related_events": ["shot"],
            "goalkeeper": {"type": {"name": "Shot Saved"}, "outcome": {"name": "Success"}},
        }
    )
    result = execute(query(metrics=tuple(m.id for m in METRICS)), d)
    assert len(result.rows) == 24
    assert all(len(row.values) == len(METRICS) for row in result.rows)
    player = mapping(next(row for row in result.rows if row.player_id == "106"))
    assert player["dribbles.attempted"].value == 2
    assert player["ball_control.completed_dribble"].value == 1
    assert player["goalkeepers.counts.on_target_saves"].status == "not_applicable"
