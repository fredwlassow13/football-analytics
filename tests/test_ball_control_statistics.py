import pytest

from football_analytics.ingestion.ball_control_statistics import (
    build_ball_control_statistics,
)
from football_analytics.integrations.base import (
    MatchBallControlIncident,
    MatchBallRecovery,
    MatchEvent,
    MatchEventPressureMetadata,
    MatchEventSpatial,
    MatchPass,
)


def action(index=1, event_type="Miscontrol", **attributes):
    models = {
        "Miscontrol": MatchBallControlIncident,
        "Dispossessed": MatchBallControlIncident,
        "Pass": MatchPass,
        "Ball Recovery": MatchBallRecovery,
    }
    return models[event_type].model_validate(
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
        }
        | attributes
    )


def header(event):
    return {field: getattr(event, field) for field in MatchEvent.model_fields}


def spatial_for(event, point=(50.0, 40.0)):
    return MatchEventSpatial.model_validate(
        header(event)
        | {
            "coordinate_system": "statsbomb_120_80",
            "start_location": (None if point is None else {"x": point[0], "y": point[1]}),
        }
    )


def pressure_for(event, **overrides):
    return MatchEventPressureMetadata.model_validate(
        header(event)
        | {
            "under_pressure": getattr(event, "under_pressure", None),
            "counterpress": getattr(event, "counterpress", None),
        }
        | overrides
    )


def build(actions, periods=None, spatial=None, pressure=None):
    return build_ball_control_statistics(
        actions,
        [spatial_for(event) for event in actions] if spatial is None else spatial,
        [pressure_for(event) for event in actions] if pressure is None else pressure,
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
        available_periods=(1, 2, 3, 4),
        periods=periods,
    )


def test_groups_teams_players_and_source_ids():
    first = action(1)
    second = action(2, period=2)
    third = action(3, external_team_id="771", external_player_id="player-2")
    result = build([third, second, first])

    assert result.event_count == 3
    assert result.external_event_ids == ("event-1", "event-2", "event-3")
    assert len(result.players) == len(result.teams) == 2
    assert sum(player.event_count for player in result.players) == 3
    assert sum(team.event_count for team in result.teams) == 3

    for summary in (result, *result.players, *result.teams):
        ids = [event_id for cell in summary.cells for event_id in cell.external_event_ids]
        assert len(ids) == len(set(ids)) == summary.event_count
        assert set(ids) == set(summary.external_event_ids)


def test_preserves_three_flag_states_in_separate_cells():
    actions = [action(index, "Pass", outcome="complete") for index in (1, 2, 3)]
    pressure = [
        pressure_for(actions[0], under_pressure=True),
        pressure_for(actions[1], under_pressure=False),
        pressure_for(actions[2]),
    ]
    result = build(actions, pressure=pressure)

    assert result.under_pressure.model_dump() == {"true": 1, "false": 1, "unknown": 1}
    assert result.counterpress.unknown == 3
    assert len(result.cells) == 3
    assert {cell.under_pressure: cell.external_event_ids for cell in result.cells} == {
        True: ("event-1",),
        False: ("event-2",),
        None: ("event-3",),
    }


def test_preserves_outcome_and_recovery_flag_distinctions():
    result = build(
        [
            action(1, "Pass", outcome="incomplete"),
            action(2, "Pass", outcome="out"),
            action(3, "Ball Recovery"),
            action(4, "Ball Recovery", recovery_failure=False),
        ]
    )

    assert len(result.cells) == 4
    assert {(cell.category, cell.reported_outcome) for cell in result.cells} == {
        ("non_completed_pass", "incomplete"),
        ("non_completed_pass", "out"),
        ("recovery_failure_unknown", None),
        ("recovery_failure_false", None),
    }


def test_preserves_unavailable_zone_without_losing_event():
    event = action()
    result = build([event], spatial=[spatial_for(event, None)])

    assert result.event_count == 1
    assert result.zone_unavailable_event_ids == ("event-1",)
    assert result.cells[0].longitudinal_zone is None
    assert result.cells[0].lateral_zone is None


def test_period_parts_recompose_totals():
    actions = [action(index, period=index) for index in range(1, 5)]
    total = build(actions)
    parts = [build(actions, (period,)) for period in range(1, 5)]

    assert sum(part.event_count for part in parts) == total.event_count
    assert (
        tuple(event_id for part in parts for event_id in part.external_event_ids)
        == total.external_event_ids
    )

    for periods in ((1, 2), (3, 4), (1, 2, 3, 4)):
        assert build(actions, periods).event_count == len(periods)


def test_empty_scope_does_not_invent_players():
    result = build([action()], (2,))
    assert result.event_count == 0
    assert result.cells == result.players == result.teams == ()
    assert result.under_pressure.unknown == 0


def test_separates_shootout_actions():
    result = build([action(period=5)])
    assert result.event_count == 0
    assert result.excluded_shootout_action_event_ids == ("event-1",)


@pytest.mark.parametrize("periods", [(), (1, 1), (5,), (True,)])
def test_rejects_invalid_periods(periods):
    with pytest.raises(ValueError):
        build([], periods)


def test_rejects_missing_metadata():
    with pytest.raises(ValueError, match="coverage mismatch"):
        build([action()], pressure=[])


def test_rejects_missing_action_coverage():
    event = action()
    with pytest.raises(ValueError, match="Control action coverage mismatch"):
        build(
            [],
            spatial=[spatial_for(event)],
            pressure=[pressure_for(event)],
        )


def test_rejects_duplicates():
    event = action()
    with pytest.raises(ValueError, match="Duplicate source"):
        build([event, event])


def test_validates_headers_outside_selected_period():
    event = action(period=2)
    metadata = pressure_for(event, external_player_id="other-player")

    with pytest.raises(ValueError, match="Metadata header mismatch"):
        build([event], (1,), pressure=[metadata])


def test_rejects_conflicting_existing_flags():
    event = action(under_pressure=True)

    with pytest.raises(ValueError, match="Pressure attribute mismatch"):
        build([event], pressure=[pressure_for(event, under_pressure=False)])


def test_rejects_missing_actor():
    with pytest.raises(ValueError, match="require actor"):
        build([action(external_player_id=None)])


def test_rejects_player_assigned_to_multiple_teams():
    with pytest.raises(ValueError, match="multiple teams"):
        build([action(1), action(2, external_team_id="771")])
