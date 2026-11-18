import pytest

from football_analytics.ingestion.movement_statistics import (
    METRICS,
    MovementStatisticsResult,
    build_movement_statistics,
)
from football_analytics.integrations.base import (
    EventLocation,
    MatchEvent,
    MatchEventSpatial,
    MatchPass,
)


def action(
    index: int,
    *,
    event_type: str = "Pass",
    period: int = 1,
    team: str = "779",
    player: str = "player-1",
) -> MatchEventSpatial:
    return MatchEventSpatial(
        provider="statsbomb",
        external_match_id="3869685",
        external_event_id=f"event-{index}",
        source_index=index,
        period=period,
        timestamp_ms=index * 1000,
        event_type=event_type,
        external_team_id=team,
        external_player_id=player,
        coordinate_system="statsbomb_120_80",
        start_location=EventLocation(x=70.0, y=40.0),
        end_location=EventLocation(x=110.0, y=40.0),
    )


def pass_for(
    event: MatchEventSpatial,
    outcome: str = "complete",
) -> MatchPass:
    header = {field: getattr(event, field) for field in MatchEvent.model_fields}
    return MatchPass.model_validate(header | {"outcome": outcome})


def build(
    spatial: list[MatchEventSpatial],
    passes: list[MatchPass],
    periods: tuple[int, ...] | None = None,
) -> MovementStatisticsResult:
    return build_movement_statistics(
        spatial,
        passes,
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
        available_periods=(1, 2, 3, 4),
        periods=periods,
    )


def test_groups_players_and_teams_with_separate_action_types() -> None:
    first = action(1)
    second = action(2, event_type="Carry", period=2)
    third = action(3, team="771", player="player-2")
    result = build(
        [third, second, first],
        [pass_for(third), pass_for(first)],
    )

    assert result.passes.external_event_ids == ("event-1", "event-3")
    assert result.carries.external_event_ids == ("event-2",)
    assert len(result.players) == len(result.teams) == 2
    assert result.passes.metrics["countable_progression"].true == 2
    assert result.carries.metrics["countable_progression"].true == 1

    for event_type in ("passes", "carries"):
        total = getattr(result, event_type)
        assert (
            sum(getattr(player, event_type).event_count for player in result.players)
            == total.event_count
        )
        assert (
            sum(getattr(team, event_type).event_count for team in result.teams) == total.event_count
        )

    other_player = next(
        player for player in result.players if player.external_player_id == "player-2"
    )
    assert other_player.carries.event_count == 0
    assert other_player.carries.metrics["countable_progression"].unknown == 0


def test_unknown_and_failed_passes_preserve_metric_ids() -> None:
    first, second, third = action(1), action(2), action(3)
    result = build(
        [first, second, third],
        [
            pass_for(first),
            pass_for(second, "incomplete"),
            pass_for(third, "unknown"),
        ],
    )

    for name in (
        "countable_progression",
        "countable_final_third_entry",
        "countable_penalty_area_entry",
    ):
        metric = result.passes.metrics[name]
        assert (metric.true, metric.false, metric.unknown) == (1, 1, 1)
        assert metric.true_event_ids == ("event-1",)
        assert metric.false_event_ids == ("event-2",)
        assert metric.unknown_event_ids == ("event-3",)

    assert result.passes.metrics["geometrically_progressive"].true == 3
    assert result.passes.pass_outcome_event_ids["unknown"] == ("event-3",)


def test_period_parts_recompose_counts_and_source_ids() -> None:
    spatial = [action(index, period=index) for index in range(1, 5)]
    passes = [pass_for(event) for event in spatial]
    total = build(spatial, passes)
    parts = [build(spatial, passes, (period,)) for period in range(1, 5)]

    assert (
        tuple(event_id for part in parts for event_id in part.passes.external_event_ids)
        == total.passes.external_event_ids
    )

    for name in METRICS:
        total_metric = total.passes.metrics[name]
        for field in ("true", "false", "unknown"):
            assert sum(getattr(part.passes.metrics[name], field) for part in parts) == getattr(
                total_metric, field
            )

    for periods in ((1, 2), (3, 4), (1, 2, 3, 4)):
        scoped = build(spatial, passes, periods)
        assert scoped.passes.event_count == len(periods)


def test_unavailable_geometry_is_preserved_in_summary() -> None:
    data = action(1, event_type="Carry").model_dump()
    data["end_location"] = None
    event = MatchEventSpatial.model_validate(data)
    result = build([event], [])

    assert result.carries.geometry_unavailable_event_ids == ("event-1",)
    assert result.carries.direction_event_ids == {"unknown": ("event-1",)}
    assert result.carries.metrics["eligible_for_count"].true == 1
    assert result.carries.metrics["countable_progression"].unknown == 1


def test_shootout_is_excluded_and_identified() -> None:
    event = action(1, period=5)
    result = build([event], [pass_for(event)])

    assert result.passes.event_count == result.carries.event_count == 0
    assert result.players == result.teams == ()
    assert result.excluded_shootout_movement_event_ids == ("event-1",)


@pytest.mark.parametrize("periods", [(), (1, 1), (5,), (True,)])
def test_rejects_invalid_period_selection(periods: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        build([], [], periods)


def test_rejects_missing_pass_coverage() -> None:
    with pytest.raises(ValueError, match="coverage mismatch"):
        build([action(1)], [])


def test_rejects_duplicate_source() -> None:
    event = action(1, event_type="Carry")
    with pytest.raises(ValueError, match="Duplicate spatial"):
        build([event, event], [])


def test_rejects_header_mismatch_even_outside_selected_period() -> None:
    event = action(1, period=2)
    data = pass_for(event).model_dump()
    data["external_player_id"] = "another-player"

    with pytest.raises(ValueError, match="Pass header mismatch"):
        build([event], [MatchPass.model_validate(data)], (1,))


def test_rejects_missing_actor() -> None:
    data = action(1, event_type="Carry").model_dump()
    data["external_player_id"] = None

    with pytest.raises(ValueError, match="require actor"):
        build([MatchEventSpatial.model_validate(data)], [])


def test_rejects_player_assigned_to_multiple_teams() -> None:
    first = action(1, event_type="Carry")
    second = action(2, event_type="Carry", team="771")

    with pytest.raises(ValueError, match="multiple teams"):
        build([first, second], [])


def test_empty_scope_does_not_invent_players() -> None:
    event = action(1)
    result = build([event], [pass_for(event)], (2,))

    assert result.players == result.teams == ()
    assert result.passes.event_count == result.carries.event_count == 0
    for name in METRICS:
        metric = result.passes.metrics[name]
        assert (metric.true, metric.false, metric.unknown) == (0, 0, 0)
