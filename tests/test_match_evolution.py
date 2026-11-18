import pytest
from test_creation_statistics import pass_action, shot_action

from football_analytics.ingestion.match_evolution import (
    build_match_evolution,
    select_score_state_events,
)
from football_analytics.integrations.base import MatchEvent, MatchEventRelations
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary
from football_analytics.visualization.match_evolution import render_match_evolution


def event(index, **changes):
    return MatchEvent.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_event_id": f"event-{index}",
            "source_index": index,
            "period": 1,
            "timestamp_ms": index * 1000,
            "event_type": "Pressure",
            "external_team_id": "779",
            **changes,
        }
    )


def build(actions=(), extra=(), links=None):
    headers = [
        MatchEvent.model_validate(a.model_dump(include=set(MatchEvent.model_fields)))
        for a in actions
    ]
    events = [*headers, *extra]
    relations = [
        MatchEventRelations.model_validate(
            e.model_dump()
            | {
                "related_event_ids": (links or {}).get(e.external_event_id),
            }
        )
        for e in events
    ]
    return build_match_evolution(
        events,
        [a for a in actions if a.event_type == "Shot"],
        relations,
        actions,
        [
            PeriodBoundary(period=1, duration_ms=100000, start_elapsed_ms=0, end_elapsed_ms=100000),
            PeriodBoundary(
                period=2, duration_ms=120000, start_elapsed_ms=100000, end_elapsed_ms=220000
            ),
        ],
        provider="statsbomb",
        match_id="3869685",
        team_ids=("779", "771"),
        events_sha256="a" * 64,
    )


def test_goal_is_attributed_to_pre_event_score_and_next_action_to_lead():
    actions = [
        pass_action(),
        shot_action(outcome="goal", statsbomb_xg=0.3),
        shot_action(3, pass_id=None, statsbomb_xg=0.2),
        shot_action(4, pass_id=None, external_team_id="771", outcome="goal", statsbomb_xg=0.4),
        shot_action(5, pass_id=None, period=2, statsbomb_xg=0.1),
    ]
    result = build(actions)
    assert [e.score_state for e in result.events] == [
        "drawing",
        "drawing",
        "leading",
        "trailing",
        "drawing",
    ]
    assert result.events[-1].score_after == (1, 1)
    assert result.events[-1].elapsed_ms == 105000
    assert result.points[-1].xg == pytest.approx(0.6)
    assert result.points[-1].created_shots == 1
    groups = {(g.external_team_id, g.score_state): g for g in result.by_score_state}
    assert groups["779", "drawing"].shots == 2
    assert groups["779", "leading"].shots == 1
    selected = select_score_state_events(actions, result, team_id="779", state="leading")
    assert [e.external_event_id for e in selected] == ["event-3"]


def test_same_timestamp_uses_source_index_and_shootout_is_excluded():
    result = build(
        [
            shot_action(1, pass_id=None, outcome="goal"),
            shot_action(2, pass_id=None, timestamp_ms=1000),
            shot_action(3, pass_id=None, period=5, outcome="goal"),
        ]
    )
    assert result.events[1].score_before == (1, 0)
    assert result.events[-1].score_after == (1, 0)
    assert result.excluded_shootout_event_ids == ("event-3",)
    assert result.points[-1].shots == 2


def test_own_goal_pair_changes_score_once_at_first_perspective():
    result = build(
        extra=[
            event(1, event_type="Own Goal Against"),
            event(2, event_type="Own Goal For", external_team_id="771", timestamp_ms=1000),
            event(3),
        ],
        links={"event-1": ("event-2",)},
    )
    assert [r.score_after for r in result.events] == [(0, 1)] * 3
    assert result.events[0].scoring_team_id == "771"
    assert result.events[1].scoring_team_id is None
    assert result.events[-1].score_state == "trailing"


def test_ambiguous_goal_does_not_publish_false_score_states():
    result = build(
        [shot_action(pass_id=None, statsbomb_xg=0.4)],
        extra=[event(3, event_type="Own Goal Against")],
    )
    assert result.goal_evidence.status == "review"
    assert all(r.score_before is None and r.score_state == "unknown" for r in result.events)
    assert result.points[-1].xg == 0.4
    assert (
        next(
            g
            for g in result.by_score_state
            if g.external_team_id == "779" and g.score_state == "unknown"
        ).shots
        == 1
    )


def test_missing_xg_keeps_partial_sum_and_null_complete_total():
    result = build(
        [
            shot_action(1, pass_id=None, statsbomb_xg=0.2),
            shot_action(2, pass_id=None),
            shot_action(3, pass_id=None, statsbomb_xg=0.3),
        ]
    )
    assert result.points[0].xg == 0.2
    assert result.points[-1].xg is None
    assert result.points[-1].xg_available == 0.5
    assert result.points[-1].missing_xg == 1
    figure = render_match_evolution(result, {})
    assert len(figure.axes) == 3
    assert "parcial" in figure.axes[0].get_legend_handles_labels()[1][0]
    figure.clear()


def test_creation_review_not_counted_and_scoped_to_pass_instant():
    result = build(
        [
            pass_action(),
            shot_action(outcome="goal"),
            pass_action(3, shot_id="event-4", outcome="incomplete"),
            shot_action(4, pass_id="event-3"),
        ]
    )
    assert result.points[-1].created_shots == 1
    assert result.creation_review_pass_ids == ("event-3",)
    drawing = next(
        g
        for g in result.by_score_state
        if g.external_team_id == "779" and g.score_state == "drawing"
    )
    assert drawing.creation_pass_event_ids == ("event-1",)


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "other"},
        {"timestamp_ms": 100001},
        {"external_team_id": "other"},
        {"period": 6},
    ],
)
def test_rejects_inconsistent_source(changes):
    with pytest.raises(ValueError):
        build(extra=[event(1, **changes)])


def test_rejects_duplicates_and_bad_selection():
    with pytest.raises(ValueError, match="Duplicate"):
        build(extra=[event(1), event(1)])
    action = shot_action(pass_id=None)
    result = build([action])
    for periods in ((), (5,), (1, 1), (True,)):
        with pytest.raises(ValueError):
            select_score_state_events(
                [action], result, team_id="779", state="drawing", periods=periods
            )
    with pytest.raises(ValueError, match="differs"):
        select_score_state_events(
            [action.model_copy(update={"timestamp_ms": 3})], result, team_id="779", state="drawing"
        )


def test_missing_offensive_team_is_not_silently_dropped():
    with pytest.raises(ValueError, match="requires a team"):
        build([shot_action(pass_id=None, external_team_id=None)])


def test_creation_diagnostics_are_preserved():
    result = build([pass_action(shot_id="missing"), shot_action(pass_id=None)])
    assert result.creation_evidence.resolution.reference_issues
    assert result.points[-1].created_shots == 0


def test_goal_clock_regression_is_diagnosed_without_reordering_source():
    result = build(
        [
            shot_action(1, pass_id=None, outcome="goal", timestamp_ms=2000),
            shot_action(2, pass_id=None, timestamp_ms=1000),
        ]
    )
    assert "source_clock_not_monotonic" in result.issues
    assert result.events[-1].score_before == (1, 0)
