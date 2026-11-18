from decimal import Decimal

import pytest
from test_possession_metadata import event, metadata

from football_analytics.ingestion.attack_sequences import build_attack_sequences
from football_analytics.integrations.base import (
    MatchEvent,
    MatchFoulCommitted,
    MatchFoulWon,
    MatchPass,
    MatchShot,
)
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary


def build(rows, *, overrides=None, passes_override=None):
    contexts = [event(i + 1, **row) for i, row in enumerate(rows)]
    changes = overrides or {}
    details = [metadata(item, **changes.get(item.source_index, {})) for item in contexts]
    passes, shots, fouls = [], [], []
    for item in contexts:
        header = item.model_dump(include=set(MatchEvent.model_fields))
        if item.event_type == "Pass":
            passes.append(
                MatchPass(**header, outcome="complete", **changes.get(-item.source_index, {}))
            )
        if item.event_type == "Shot":
            shots.append(
                MatchShot(
                    **header,
                    outcome="goal" if item.source_index in changes.get("goals", ()) else "saved",
                    shot_type=changes.get("shot_type", "Open Play"),
                )
            )
        if item.event_type in {"Foul Committed", "Foul Won"}:
            cls = MatchFoulCommitted if item.event_type == "Foul Committed" else MatchFoulWon
            fouls.append(
                cls(
                    **header,
                    **changes.get("fouls", {}).get(
                        item.source_index, {"advantage": changes.get("advantage")}
                    ),
                )
            )
    return build_attack_sequences(
        contexts,
        details,
        passes if passes_override is None else passes_override,
        shots,
        fouls,
        [
            PeriodBoundary(
                period=i, duration_ms=1000, start_elapsed_ms=(i - 1) * 1000, end_elapsed_ms=i * 1000
            )
            for i in (1, 2)
        ],
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
    )


def test_same_possession_is_split_at_out_and_explicit_restart() -> None:
    result = build(
        [{}, {}, {}, {"event_type": "Half End"}],
        overrides={
            -1: {"pass_type": "Kick Off"},
            2: {"out": True, "duration_seconds": 0.025},
            -3: {"pass_type": "Throw-in"},
        },
    )
    assert [a.external_event_ids for a in result.attacks] == [("event-1", "event-2"), ("event-3",)]
    assert [a.status for a in result.attacks] == ["bounded", "bounded"]
    stop = result.stoppages[0]
    assert stop.stop_event_ids == ("event-2",)
    assert stop.restart_event_id == "event-3"
    assert stop.duration_ms == Decimal(75)
    assert result.possession_percentage is None


def test_no_duration_means_no_stoppage_duration() -> None:
    result = build([{}, {}], overrides={1: {"out": True}, -2: {"pass_type": "Corner"}})
    assert result.stoppages[0].duration_ms is None
    assert "missing_stop_time" in result.stoppages[0].issues


def test_clock_regression_is_preserved_not_sorted_away() -> None:
    result = build(
        [{"timestamp_ms": 500}, {"timestamp_ms": 100}, {"event_type": "Half End"}],
        overrides={-1: {"pass_type": "Kick Off"}},
    )
    assert result.attacks[0].external_event_ids == ("event-1", "event-2")
    assert result.attacks[0].observed_span_ms is None
    assert "timestamp_regression" in result.attacks[0].issues


def test_goal_ends_attack_but_saved_shot_allows_rebound() -> None:
    rows = [
        {},
        {"event_type": "Shot"},
        {},
        {"event_type": "Shot"},
        {"event_type": "Goal Keeper"},
        {},
        {"event_type": "Half End"},
    ]
    result = build(
        rows,
        overrides={
            "goals": (4,),
            4: {"duration_seconds": 0.01},
            -1: {"pass_type": "Kick Off"},
            -6: {"pass_type": "Kick Off"},
        },
    )
    assert result.attacks[0].shot_event_ids == ("event-2", "event-4")
    assert result.attacks[0].end_reason == "goal"
    assert "event-5" in result.unassigned_event_ids
    assert result.stoppages[0].closure == "explicit_restart"


def test_advantage_does_not_stop_play_and_absence_policy_is_visible() -> None:
    rows = [{}, {"event_type": "Foul Committed"}, {}, {"event_type": "Half End"}]
    result = build(rows, overrides={"advantage": True})
    assert len(result.attacks) == 1
    assert result.stoppages == ()
    result = build(rows)
    assert result.boundary_evidence[0].certainty == "provider_absence_policy"
    assert result.stoppages[0].closure == "unobserved_restart"
    assert result.stoppages[0].duration_ms is None


def test_gap_alone_does_not_become_dead_ball_time() -> None:
    result = build([{}, {"timestamp_ms": 900}])
    assert result.stoppages == ()
    assert result.attacks[0].end_reason == "source_end"
    assert result.attacks[0].status == "review"


def test_injury_marker_is_a_stop_even_without_in_chain_metadata() -> None:
    result = build(
        [{}, {"event_type": "Injury Stoppage"}, {}], overrides={-3: {"pass_type": "Free Kick"}}
    )
    assert result.stoppages[0].reasons == ("injury_stoppage",)
    assert result.stoppages[0].duration_ms == Decimal(100)


def test_turnover_splits_and_opponent_pressure_does_not() -> None:
    result = build(
        [
            {},
            {"event_type": "Pressure", "external_team_id": "771"},
            {
                "external_team_id": "771",
                "external_possession_team_id": "771",
                "possession_sequence": 2,
            },
            {"event_type": "Half End"},
        ]
    )
    assert result.attacks[0].external_event_ids == ("event-1", "event-2")
    assert result.attacks[0].end_reason == "possession_change"
    assert result.attacks[1].external_team_id == "771"


def test_same_team_new_source_possession_is_uncertain_boundary() -> None:
    result = build([{}, {"possession_sequence": 2}])
    assert len(result.attacks) == 2
    assert result.attacks[0].end_reason == "source_boundary"
    assert result.attacks[1].start_reason == "source_boundary"


def test_no_cross_period_stoppage_or_shootout_attack() -> None:
    result = build([{}, {"period": 2}, {"period": 5}], overrides={1: {"out": True}})
    assert result.stoppages[0].closure == "source_end"
    assert result.stoppages[0].duration_ms is None
    assert result.excluded_shootout_event_ids == ("event-3",)
    assert len(result.attacks) == 2


def test_exact_source_coverage_required() -> None:
    with pytest.raises(ValueError, match="coverage"):
        build([{}], passes_override=[])


def test_all_events_partition_assigned_or_unassigned() -> None:
    result = build(
        [
            {"event_type": "Starting XI"},
            {},
            {"event_type": "Tactical Shift"},
            {},
            {"event_type": "Half End"},
        ]
    )
    assigned = [key for attack in result.attacks for key in attack.external_event_ids]
    assert len(assigned) == len(set(assigned))
    assert set(assigned).isdisjoint(result.unassigned_event_ids)
    assert set(assigned) | set(result.unassigned_event_ids) == set(result.external_event_ids)


def test_missing_half_end_is_not_a_complete_period_boundary() -> None:
    result = build([{}, {"period": 2}], overrides={-1: {"pass_type": "Kick Off"}})
    assert result.attacks[0].end_reason == "source_end"
    assert result.attacks[0].status == "review"


def test_negative_interval_remains_missing() -> None:
    result = build(
        [{}, {}],
        overrides={1: {"out": True, "duration_seconds": 0.2}, -2: {"pass_type": "Throw-in"}},
    )
    assert "negative_interval" in result.stoppages[0].issues
    assert result.stoppages[0].duration_ms is None
    assert result.stoppages[0].start_timestamp_ms == Decimal(300)
    assert result.stoppages[0].end_timestamp_ms == 200


def test_unknown_owner_activity_invalidates_pending_dead_ball_window() -> None:
    result = build(
        [{}, {"external_possession_team_id": None}, {}],
        overrides={1: {"out": True, "duration_seconds": 0}, -3: {"pass_type": "Throw-in"}},
    )
    assert result.stoppages[0].closure == "unobserved_restart"
    assert result.stoppages[0].duration_ms is None


def test_recovery_alone_does_not_assert_control_at_start() -> None:
    result = build([{"event_type": "Ball Recovery"}, {}])
    assert result.attacks[0].external_event_ids == ("event-2",)
    assert result.unassigned_event_ids == ("event-1",)


def test_shot_penalty_is_a_restart_and_shootout_remains_excluded() -> None:
    result = build(
        [{"event_type": "Shot"}, {"period": 5, "event_type": "Shot"}],
        overrides={"shot_type": "Penalty", "goals": (1,), 1: {"duration_seconds": 0.1}},
    )
    assert result.attacks[0].start_reason == "restart"
    assert result.attacks[0].end_reason == "goal"
    assert result.attacks[0].shot_event_ids == ("event-1",)
    assert result.excluded_shootout_event_ids == ("event-2",)


def test_duplicate_injury_signals_form_one_stoppage() -> None:
    result = build(
        [{}, {"event_type": "Injury Stoppage"}, {"event_type": "Injury Stoppage"}, {}],
        overrides={-4: {"pass_type": "Free Kick"}},
    )
    assert len(result.stoppages) == 1
    assert result.stoppages[0].stop_event_ids == ("event-2", "event-3")
    assert result.stoppages[0].duration_ms == Decimal(200)


def test_advantage_in_linked_foul_counterpart_is_respected() -> None:
    result = build(
        [
            {},
            {"event_type": "Foul Committed"},
            {"event_type": "Foul Won"},
            {},
            {"event_type": "Half End"},
        ],
        overrides={
            "fouls": {
                2: {"related_event_ids": ("event-3",)},
                3: {"advantage": True},
            }
        },
    )
    assert len(result.attacks) == 1
    assert result.stoppages == ()


def test_foul_pair_is_one_interruption() -> None:
    result = build(
        [{}, {"event_type": "Foul Committed"}, {"event_type": "Foul Won"}, {}],
        overrides={-4: {"pass_type": "Free Kick"}},
    )
    assert len(result.stoppages) == 1
    assert result.stoppages[0].stop_event_ids == ("event-2", "event-3")


def test_empty_input_keeps_provenance_and_no_possession_estimate() -> None:
    result = build([])
    assert result.attacks == ()
    assert result.stoppages == ()
    assert result.events_sha256 == "a" * 64
    assert result.attack_policy_version == "1.0.0"
    assert result.possession_duration_ms is None


def test_control_by_opponent_splits_even_if_source_possession_owner_did_not_change() -> None:
    result = build([{}, {"external_team_id": "771"}, {}])
    assert [a.external_team_id for a in result.attacks] == ["779", "771", "779"]
    assert result.attacks[0].end_reason == "possession_change"
    assert "source_possession_team_disagreement" in result.attacks[1].issues
