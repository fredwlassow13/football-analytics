import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from test_score_state_execution import data, execute

from football_analytics.analytics.catalogue import METRICS as CATALOGUE
from football_analytics.analytics.score_state_execution import (
    ScoreStateQuery,
    ScoreStateResult,
    prepare_score_state,
    score_state_value,
)

METRICS = tuple(m.id for m in CATALOGUE if m.family in {"movement", "ball_control"})


def query(**changes):
    return ScoreStateQuery(
        **(
            {
                "match_ids": (123,),
                "group_by": "team",
                "metrics": METRICS,
                "score_state": "drawing",
                "score_state_contract_version": "1.4.0",
            }
            | changes
        )
    )


def source():
    return data(
        extra=[
            ("Pass", 1, 1, {"location": [20, 40], "pass": {"end_location": [50, 40]}}),
            (
                "Shot",
                1,
                1,
                {
                    "location": [110, 40],
                    "shot": {
                        "end_location": [120, 40, 1],
                        "outcome": {"name": "Goal"},
                        "type": {"name": "Open Play"},
                        "statsbomb_xg": 0.2,
                    },
                },
            ),
            ("Carry", 1, 1, {"location": [60, 40], "carry": {"end_location": [90, 40]}}),
            ("Miscontrol", 2, 1, {"location": [30, 40], "under_pressure": True}),
            ("Pass", 2, 2, {"location": [130, 40], "pass": {"end_location": [50, 40]}}),
        ]
    )


def context(q=None, src=None):
    ref, events, _ = src or source()
    return prepare_score_state(
        events,
        query=q or query(),
        match_id=123,
        team_ids=("1", "2"),
        events_sha256=ref.events_sha256,
    )


def scope(c, team, family):
    return next(
        s.selected_event_ids
        for s in c.complementary_scopes
        if s.team_id == team and s.family == family
    )


@pytest.mark.parametrize(
    "state,team,movement,control",
    [
        ("drawing", "1", ("e3",), ("e3",)),
        ("leading", "1", ("e5",), ()),
        ("trailing", "2", ("e7",), ("e6", "e7")),
        ("leading", "2", (), ()),
    ],
)
def test_pre_action_state_and_team_perspective(state, team, movement, control):
    c = context(query(score_state=state))
    assert scope(c, team, "movement") == movement
    assert scope(c, team, "ball_control") == control


def test_unknown_geometry_is_explicit_classification():
    c = context(query(score_state="trailing"))
    name = "movement.pass.geometrically_progressive.unknown"
    v = score_state_value(name, c, "2")
    assert v.value == 1 and v.status == "available"
    assert "movement_geometry_unavailable" in v.review_reasons
    assert v.coverage.eligible_events == 1


def test_period_filter_uses_complete_score_history():
    c = context(query(score_state="trailing", periods=(2,)))
    assert scope(c, "2", "ball_control") == ("e7",)
    assert scope(c, "2", "movement") == ("e7",)


def test_shootout_excluded():
    ref, events, lineups = source()
    extra = dict(next(e for e in events if e["id"] == "e3"))
    extra.update(id="shootout-pass", index=max(e["index"] for e in events) + 1, period=5)
    events.append(extra)
    ref = ref.model_copy(
        update={"events_sha256": hashlib.sha256(json.dumps(events).encode()).hexdigest()}
    )
    c = context(src=(ref, events, lineups))
    assert all("shootout-pass" not in s.selected_event_ids for s in c.complementary_scopes)


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0", "1.3.0"])
def test_explicit_extension_required(version):
    with pytest.raises(ValueError, match="1.4.0"):
        query(score_state_contract_version=version)


def test_serialized_evidence_required_for_both_teams():
    result = execute(query(), source())
    assert ScoreStateResult.model_validate_json(result.model_dump_json()) == result
    body = result.model_dump(mode="json")
    body["score_evidence"][0]["complementary_scope"].pop()
    with pytest.raises(ValueError, match="both source teams"):
        ScoreStateResult.model_validate_json(json.dumps(body))


def test_no_events_is_known_zero():
    c = context(query(score_state="leading"))
    for metric in METRICS:
        v = score_state_value(metric, c, "2")
        assert v.value == 0 and v.status == "available"
        assert v.coverage.eligible_events == 0


def test_state_partitions_reproduce_all_actions():
    seen = {family: [] for family in ("movement", "ball_control")}
    for state in ("leading", "drawing", "trailing"):
        c = context(query(score_state=state))
        for s in c.complementary_scopes:
            seen[s.family].extend(s.selected_event_ids)
    assert sorted(seen["movement"]) == ["e3", "e5", "e7"]
    assert sorted(seen["ball_control"]) == ["e3", "e6", "e7"]


def load_verifier():
    path = Path(__file__).resolve().parents[1] / "scripts/verify_score_state_complementary.py"
    spec = importlib.util.spec_from_file_location("verify_complementary", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reference_counts_and_detects_catalogue_score_mismatch(tmp_path):
    ref, events, lineups = source()
    events_path = tmp_path / "123.json"
    lineups_path = tmp_path / "lineups.json"
    events_path.write_text(json.dumps(events))
    lineups_path.write_text(json.dumps(lineups))
    match = {
        "match_id": 123,
        "home_team": {"home_team_id": 1},
        "away_team": {"away_team_id": 2},
        "home_score": 1,
        "away_score": 0,
    }
    verifier = load_verifier()
    report = verifier.verify_match(events_path, match, lineups_path)
    assert report["comparisons"] == len(METRICS) * 6
    with pytest.raises(ValueError, match="match catalogue"):
        verifier.reference(events, match | {"home_score": 2})


def test_unresolved_goal_prevents_counts():
    ref, events, lineups = source()
    goal = next(e for e in events if e["id"] == "e4")
    goal["type"] = {"name": "Own Goal Against"}
    goal.pop("shot")
    ref = ref.model_copy(
        update={"events_sha256": hashlib.sha256(json.dumps(events).encode()).hexdigest()}
    )
    c = context(src=(ref, events, lineups))
    assert c.evolution.goal_evidence.status == "review"
    for metric in METRICS:
        v = score_state_value(metric, c, "1")
        assert v.status == "unknown" and v.value is None


def test_selected_matches_and_mixed_families():
    result = execute(
        query(metrics=(*METRICS, "passes.attempted"), aggregation="selected_matches"), source()
    )
    assert result.score_state_contract_version == "1.4.0"
    assert len(result.rows) == 2
    assert next(v.value for v in result.rows[0].values if v.metric_id == "passes.attempted") == 1


def test_verifier_database_path_rejects_wrong_value(tmp_path, monkeypatch):
    src = source()
    ref, events, lineups = src
    ep = tmp_path / "events.json"
    lp = tmp_path / "lineups.json"
    ep.write_text(json.dumps(events))
    lp.write_text(json.dumps(lineups))
    match = {
        "match_id": 123,
        "home_team": {"home_team_id": 1},
        "away_team": {"away_team_id": 2},
        "home_score": 1,
        "away_score": 0,
    }
    verifier = load_verifier()
    monkeypatch.setattr(verifier, "execute_query", lambda connection, q: execute(q, src))
    report = verifier.verify_match(ep, match, lp, object())
    assert report["comparisons"] == len(METRICS) * 6

    def wrong(connection, q):
        result = execute(q, src)
        row = result.rows[0]
        bad = row.values[0].model_copy(update={"value": 999})
        return result.model_copy(
            update={
                "rows": (
                    row.model_copy(update={"values": (bad, *row.values[1:])}),
                    *result.rows[1:],
                )
            }
        )

    monkeypatch.setattr(verifier, "execute_query", wrong)
    with pytest.raises(ValueError, match="Count/coverage mismatch"):
        verifier.verify_match(ep, match, lp, object())
