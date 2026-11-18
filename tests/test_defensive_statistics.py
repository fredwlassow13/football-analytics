import pytest

from football_analytics.ingestion.defensive_scope import (
    DefensiveEvent,
    select_defensive_periods,
)
from football_analytics.ingestion.defensive_statistics import (
    aggregate_player_defensive_actions,
)
from football_analytics.integrations.base import (
    MatchBallRecovery,
    MatchBlock,
    MatchClearance,
    MatchDribbledPast,
    MatchDuel,
    MatchFoulCommitted,
    MatchFoulWon,
    MatchInterception,
    MatchPressure,
)


def header(index: int, *, period: int = 1) -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": f"event-{index}",
        "source_index": index,
        "period": period,
        "timestamp_ms": index * 1000,
        "external_team_id": "779",
        "external_player_id": "player-1",
    }


def block(index: int, **changes: object) -> MatchBlock:
    return MatchBlock.model_validate(header(index) | changes)


def test_separates_known_false_and_unknown_flags() -> None:
    result = aggregate_player_defensive_actions(
        [
            block(3),
            block(1, offensive=True),
            block(2, offensive=False),
        ],
        available_periods=(1, 2),
    )[0]

    action = result.actions[0]

    assert result.event_count == 3
    assert result.external_event_ids == ("event-1", "event-2", "event-3")
    assert action.flags["offensive"].model_dump() == {"true": 1, "false": 1, "unknown": 1}
    assert action.flags["save_block"].unknown == 3


def test_preserves_categories_and_coverage() -> None:
    events: list[DefensiveEvent] = [
        MatchInterception.model_validate(header(1) | {"outcome": "lost_out"}),
        MatchInterception.model_validate(header(2) | {"outcome": "won"}),
        MatchPressure.model_validate(
            header(3)
            | {
                "duration_seconds": 0.0,
                "related_event_ids": (),
            }
        ),
        MatchPressure.model_validate(header(4)),
        MatchFoulCommitted.model_validate(header(5) | {"card": "yellow_card"}),
        MatchFoulCommitted.model_validate(header(6)),
    ]

    player = aggregate_player_defensive_actions(events, available_periods=(1, 2))[0]
    actions = {action.event_type: action for action in player.actions}

    outcomes = actions["Interception"].categories["outcome"]
    assert outcomes.known == {"lost_out": 1, "won": 1}
    assert outcomes.missing == 0

    pressure = actions["Pressure"]
    assert pressure.coverage["duration_seconds"].present == 1
    assert pressure.coverage["duration_seconds"].missing == 1
    assert pressure.coverage["related_event_ids"].present == 1
    assert pressure.coverage["related_event_ids"].missing == 1

    cards = actions["Foul Committed"].categories["card"]
    assert cards.known == {"yellow_card": 1}
    assert cards.missing == 1


def test_supports_all_nine_event_types_without_merging_them() -> None:
    events: list[DefensiveEvent] = [
        MatchBallRecovery.model_validate(header(1)),
        block(2),
        MatchClearance.model_validate(header(3)),
        MatchDribbledPast.model_validate(header(4)),
        MatchDuel.model_validate(header(5) | {"duel_type": "tackle", "outcome": "won"}),
        MatchFoulCommitted.model_validate(header(6)),
        MatchFoulWon.model_validate(header(7)),
        MatchInterception.model_validate(header(8) | {"outcome": "success_in_play"}),
        MatchPressure.model_validate(header(9)),
    ]

    player = aggregate_player_defensive_actions(events, available_periods=(1, 2))[0]

    assert len(player.actions) == 9
    assert player.event_count == 9
    assert sum(action.event_count for action in player.actions) == 9


def test_separates_players_and_teams() -> None:
    players = aggregate_player_defensive_actions(
        [
            block(1),
            block(2, external_player_id="player-2"),
            block(
                3,
                external_player_id="player-3",
                external_team_id="771",
            ),
        ],
        available_periods=(1, 2),
    )

    assert len(players) == 3
    assert sum(player.event_count for player in players) == 3
    assert {(player.external_team_id, player.external_player_id) for player in players} == {
        ("779", "player-1"),
        ("779", "player-2"),
        ("771", "player-3"),
    }


def test_periods_partition_extra_time_and_exclude_shootout() -> None:
    events = [block(index, period=index) for index in range(1, 6)]

    full = aggregate_player_defensive_actions(events, available_periods=(1, 2, 3, 4))[0]
    normal = aggregate_player_defensive_actions(
        events,
        available_periods=(1, 2, 3, 4),
        periods=(1, 2),
    )[0]
    extra = aggregate_player_defensive_actions(
        events,
        available_periods=(1, 2, 3, 4),
        periods=(3, 4),
    )[0]

    assert full.event_count == normal.event_count + extra.event_count
    assert full.event_count == 4
    assert full.periods == (1, 2, 3, 4)
    assert extra.external_event_ids == ("event-3", "event-4")


@pytest.mark.parametrize("periods", [(), (1, 1), (3,), (5,), (True,)])
def test_rejects_invalid_requested_periods(
    periods: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match="Requested periods"):
        select_defensive_periods([], available_periods=(1, 2), periods=periods)


@pytest.mark.parametrize("available", [(), (1,), (1, 2, 3), (1, 2, 2), (True, 2)])
def test_rejects_invalid_available_periods(
    available: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match="Available periods"):
        select_defensive_periods([], available_periods=available)


@pytest.mark.parametrize("field", ["external_event_id", "source_index"])
def test_rejects_duplicate_sources_even_outside_selected_period(
    field: str,
) -> None:
    first = block(1, period=2)
    second = block(2, period=2, **{field: getattr(first, field)})

    with pytest.raises(ValueError, match="Duplicate"):
        aggregate_player_defensive_actions(
            [first, second],
            available_periods=(1, 2),
            periods=(1,),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("provider", "other"), ("external_match_id", "other-match")],
)
def test_rejects_mixed_source_identity(field: str, value: str) -> None:
    with pytest.raises(ValueError, match="one provider and match"):
        aggregate_player_defensive_actions(
            [block(1), block(2, **{field: value})],
            available_periods=(1, 2),
        )


@pytest.mark.parametrize("field", ["external_team_id", "external_player_id"])
def test_requires_selected_player_and_team_references(field: str) -> None:
    with pytest.raises(ValueError, match="references"):
        aggregate_player_defensive_actions(
            [block(1, **{field: None})],
            available_periods=(1, 2),
        )


def test_rejects_player_assigned_to_multiple_teams() -> None:
    with pytest.raises(ValueError, match="multiple teams"):
        aggregate_player_defensive_actions(
            [block(1), block(2, external_team_id="771")],
            available_periods=(1, 2),
        )


def test_rejects_event_period_incompatible_with_match() -> None:
    with pytest.raises(ValueError, match="incompatible"):
        select_defensive_periods(
            [block(1, period=3)],
            available_periods=(1, 2),
        )


def test_does_not_create_players_without_observed_events() -> None:
    assert aggregate_player_defensive_actions([], available_periods=(1, 2)) == []

    assert (
        aggregate_player_defensive_actions(
            [block(1, period=2)],
            available_periods=(1, 2),
            periods=(1,),
        )
        == []
    )
