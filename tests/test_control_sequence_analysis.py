import pytest

from football_analytics.ingestion.control_sequence_analysis import (
    build_control_sequence_analysis,
)
from football_analytics.integrations.base import (
    MatchEvent,
    MatchEventContext,
    MatchEventPressureMetadata,
    MatchEventRelations,
    MatchShot,
)
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary


def context(index, event_type="Pass", **attributes):
    return MatchEventContext.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_event_id": f"event-{index}",
            "source_index": index,
            "period": 1,
            "timestamp_ms": index * 1000,
            "event_type": event_type,
            "external_team_id": "779",
            "external_player_id": "player-1",
            "possession_sequence": 1,
            "external_possession_team_id": "779",
        }
        | attributes
    )


def header(event):
    return {field: getattr(event, field) for field in MatchEvent.model_fields}


def streams(contexts, links=None):
    links = {} if links is None else links
    relations = [
        MatchEventRelations(
            **header(event),
            related_event_ids=links.get(event.external_event_id),
        )
        for event in contexts
    ]
    pressure = [
        MatchEventPressureMetadata(
            **header(event),
            under_pressure=event.under_pressure,
        )
        for event in contexts
    ]
    shots = [
        MatchShot(**header(event), outcome="goal")
        for event in contexts
        if event.event_type == "Shot"
    ]
    return relations, pressure, shots


def analyze(contexts, links=None, relations=None, pressure=None, shots=None):
    defaults = streams(contexts, links)
    return build_control_sequence_analysis(
        contexts,
        [
            PeriodBoundary(
                period=1,
                duration_ms=100_000,
                start_elapsed_ms=0,
                end_elapsed_ms=100_000,
            ),
            PeriodBoundary(
                period=2,
                duration_ms=100_000,
                start_elapsed_ms=100_000,
                end_elapsed_ms=200_000,
            ),
        ],
        defaults[0] if relations is None else relations,
        defaults[1] if pressure is None else pressure,
        defaults[2] if shots is None else shots,
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
    )


def test_records_shot_goal_even_when_goalkeeper_is_last():
    result = analyze(
        [
            context(1, under_pressure=True),
            context(2, "Shot"),
            context(3, "Goal Keeper", external_team_id="771"),
        ]
    )
    observation = result.observations[0]

    assert observation.control_event_ids == ("event-1",)
    assert observation.under_pressure.true_event_ids == ("event-1",)
    assert observation.shot_event_ids == ("event-2",)
    assert observation.shot_goal_event_ids == ("event-2",)
    assert observation.possession_team_shot_ids == ("event-2",)
    assert observation.last_event_type == "Goal Keeper"
    assert observation.last_event_id == "event-3"


def test_preserves_multiple_shots_in_same_fragment():
    result = analyze([context(1, "Shot"), context(2, "Shot")])
    observation = result.observations[0]
    assert observation.shot_event_ids == ("event-1", "event-2")
    assert observation.shot_outcome_event_ids == {"goal": ("event-1", "event-2")}


def test_deduplicates_pair_preserving_both_directions_and_fragments():
    result = analyze(
        [
            context(1, "Dispossessed"),
            context(
                2,
                "Duel",
                possession_sequence=2,
                external_possession_team_id="771",
                external_team_id="771",
            ),
        ],
        {"event-1": ("event-2",), "event-2": ("event-1",)},
    )

    assert len(result.relation_pairs) == 1
    pair = result.relation_pairs[0]
    assert pair.evidence == ("first_to_second", "second_to_first")
    assert pair.location == "different_fragments"
    assert (pair.first_fragment_number, pair.second_fragment_number) == (1, 2)
    assert result.observations[0].next_fragment_relation == "different_team"


def test_preserves_incoming_one_direction_relation_from_noncontrol_action():
    result = analyze(
        [context(1), context(2, "Carry")],
        {"event-2": ("event-1",)},
    )
    assert result.relation_pairs[0].evidence == ("second_to_first",)


def test_reports_missing_self_and_duplicate_references():
    result = analyze(
        [context(1), context(2, "Carry")],
        {"event-1": ("missing", "event-1", "event-2", "event-2")},
    )
    assert {issue.code for issue in result.reference_issues} == {
        "target_missing",
        "self_reference",
        "duplicate_reference",
    }
    assert len(result.relation_pairs) == 1


def test_preserves_temporal_diagnostics():
    result = analyze(
        [
            context(1),
            context(2, "Shot", timestamp_ms=500),
            context(3, "Goal Keeper"),
        ]
    )
    assert result.fragments[0].issues == ("timestamp_regression",)
    assert result.fragments[0].observed_span_ms is None
    assert result.observations[0].shot_goal_event_ids == ("event-2",)


def test_separates_shootout_and_period_boundary():
    result = analyze(
        [
            context(1),
            context(2, period=2, possession_sequence=2),
            context(3, "Shot", period=5),
        ]
    )
    assert result.excluded_shootout_event_ids == ("event-3",)
    assert len(result.observations) == 2
    assert all(
        row.next_fragment_relation == "no_next_fragment_in_period" for row in result.observations
    )
    assert all(not row.shot_event_ids for row in result.observations)


def test_separates_other_and_unknown_shooter_teams():
    result = analyze(
        [
            context(1, "Shot", external_team_id="771"),
            context(2, "Shot", external_team_id=None),
        ]
    )
    observation = result.observations[0]
    assert observation.other_team_shot_ids == ("event-1",)
    assert observation.unknown_team_shot_ids == ("event-2",)


def test_preserves_own_goal_records_separately():
    result = analyze([context(1, "Own Goal For"), context(2, "Own Goal Against")])
    observation = result.observations[0]
    assert observation.own_goal_for_event_ids == ("event-1",)
    assert observation.own_goal_against_event_ids == ("event-2",)
    assert observation.shot_goal_event_ids == ()


def test_requires_complete_aligned_streams():
    with pytest.raises(ValueError, match="coverage mismatch"):
        analyze([context(1)], pressure=[])


def test_requires_complete_shot_coverage():
    with pytest.raises(ValueError, match="coverage mismatch"):
        analyze([context(1, "Shot")], shots=[])


def test_rejects_header_mismatch():
    contexts = [context(1)]
    relations, pressure, shots = streams(contexts)
    data = pressure[0].model_dump()
    data["external_team_id"] = "771"
    pressure = [MatchEventPressureMetadata.model_validate(data)]

    with pytest.raises(ValueError, match="Aligned header mismatch"):
        analyze(contexts, relations=relations, pressure=pressure, shots=shots)


def test_rejects_pressure_context_conflict():
    contexts = [context(1, under_pressure=True)]
    data = streams(contexts)[1][0].model_dump()
    data["under_pressure"] = False

    with pytest.raises(ValueError, match="differs from event context"):
        analyze(
            contexts,
            pressure=[MatchEventPressureMetadata.model_validate(data)],
        )
