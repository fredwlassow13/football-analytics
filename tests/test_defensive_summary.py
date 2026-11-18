import pytest
from pydantic import ValidationError

from football_analytics.ingestion.defensive_scope import DefensiveEvent
from football_analytics.ingestion.defensive_summary import (
    DefensiveSummary,
    build_defensive_summary,
)
from football_analytics.integrations.base import (
    MatchAerialDuel,
    MatchClearance,
    MatchDuel,
    MatchPressure,
)


def header(
    index: int,
    event_type: str,
    **changes: object,
) -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": f"event-{index}",
        "source_index": index,
        "period": 1,
        "timestamp_ms": index * 1000,
        "event_type": event_type,
        "external_team_id": "779",
        "external_player_id": "player-1",
    } | changes


def aerial(
    index: int,
    *,
    event_type: str = "Pass",
    **changes: object,
) -> MatchAerialDuel:
    lost = event_type == "Duel"
    return MatchAerialDuel.model_validate(
        header(index, event_type)
        | {
            "outcome": "lost" if lost else "won",
            "evidence": ("aerial_lost_subtype" if lost else "aerial_won_flag"),
        }
        | changes
    )


def build(
    events: list[DefensiveEvent],
    aerials: list[MatchAerialDuel],
    *,
    periods: tuple[int, ...] | None = None,
) -> DefensiveSummary:
    return build_defensive_summary(
        events,
        aerials,
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
        available_periods=(1, 2, 3, 4),
        periods=periods,
    )


def test_unifies_sources_without_double_counting_shared_events() -> None:
    clearance = MatchClearance.model_validate(header(2, "Clearance") | {"aerial_won": True})
    lost = MatchDuel.model_validate(header(3, "Duel") | {"duel_type": "aerial_lost"})
    pressure = MatchPressure.model_validate(header(1, "Pressure"))

    result = build(
        [lost, clearance, pressure],
        [
            aerial(4),
            aerial(3, event_type="Duel"),
            aerial(2, event_type="Clearance"),
        ],
    )

    assert result.action_event_ids == ("event-1", "event-2", "event-3")
    assert result.aerial_event_ids == ("event-2", "event-3", "event-4")
    assert result.shared_event_ids == ("event-2", "event-3")
    assert result.external_event_ids == ("event-1", "event-2", "event-3", "event-4")

    player = result.players[0]
    assert player.actions is not None
    assert player.actions.event_count == 3
    assert player.duels is not None
    assert player.duels.aerial_lost == 1
    assert player.aerials is not None
    assert player.aerials.won == 2
    assert player.aerials.lost == 1
    assert player.external_event_ids == result.external_event_ids


def test_preserves_specialized_tackle_results() -> None:
    tackle = MatchDuel.model_validate(
        header(1, "Duel") | {"duel_type": "tackle", "outcome": "success_in_play"}
    )

    result = build([tackle], [])
    player = result.players[0]

    assert player.duels is not None
    assert player.duels.tackles.success_in_play == 1
    assert player.aerials is None
    assert result.shared_event_ids == ()


def test_supports_aerial_only_players_and_teams() -> None:
    result = build(
        [],
        [aerial(1, external_player_id="player-2", external_team_id="771")],
    )

    assert len(result.players) == len(result.teams) == 1
    assert result.players[0].actions is None
    assert result.players[0].duels is None
    assert result.players[0].aerials is not None
    assert result.teams[0].actions is None
    assert result.teams[0].external_team_id == "771"
    assert result.external_event_ids == ("event-1",)


def test_selects_periods_for_every_view_and_excludes_shootout() -> None:
    result = build(
        [
            MatchPressure.model_validate(header(1, "Pressure", period=1)),
            MatchPressure.model_validate(header(2, "Pressure", period=3)),
            MatchPressure.model_validate(header(3, "Pressure", period=5)),
        ],
        [aerial(4, period=3), aerial(5, period=5)],
        periods=(3, 4),
    )

    assert result.periods == (3, 4)
    assert result.action_event_ids == ("event-2",)
    assert result.aerial_event_ids == ("event-4",)
    assert result.external_event_ids == ("event-2", "event-4")


def test_preserves_provenance_and_serializable_output() -> None:
    result = build(
        [MatchPressure.model_validate(header(1, "Pressure"))],
        [],
    )

    data = result.model_dump(mode="json")
    assert data["events_sha256"] == "a" * 64
    assert data["formula_version"] == "1.0.0"
    assert data["available_periods"] == [1, 2, 3, 4]
    assert data["external_event_ids"] == ["event-1"]


def test_requires_aerial_projection_of_marked_clearance() -> None:
    clearance = MatchClearance.model_validate(header(1, "Clearance") | {"aerial_won": True})
    with pytest.raises(ValueError, match="Missing aerial projection"):
        build([clearance], [])


def test_requires_aerial_projection_of_aerial_loss() -> None:
    lost = MatchDuel.model_validate(header(1, "Duel") | {"duel_type": "aerial_lost"})
    with pytest.raises(ValueError, match="Missing aerial projection"):
        build([lost], [])


@pytest.mark.parametrize("event_type", ["Clearance", "Duel"])
def test_requires_action_counterpart_for_shared_aerial_types(
    event_type: str,
) -> None:
    with pytest.raises(ValueError, match="evidence differs"):
        build([], [aerial(1, event_type=event_type)])


def test_rejects_unmarked_clearance_with_aerial_win() -> None:
    clearance = MatchClearance.model_validate(header(1, "Clearance"))

    with pytest.raises(ValueError, match="evidence differs"):
        build([clearance], [aerial(1, event_type="Clearance")])


def test_rejects_different_headers_for_shared_source() -> None:
    clearance = MatchClearance.model_validate(header(1, "Clearance") | {"aerial_won": True})

    with pytest.raises(ValueError, match="headers differ"):
        build(
            [clearance],
            [aerial(1, event_type="Clearance", timestamp_ms=9999)],
        )


def test_rejects_index_collision_between_distinct_sources() -> None:
    pressure = MatchPressure.model_validate(header(1, "Pressure"))

    with pytest.raises(ValueError, match="Source index"):
        build(
            [pressure],
            [aerial(1, external_event_id="different-event")],
        )


def test_rejects_duplicate_aerial_input() -> None:
    event = aerial(1)
    with pytest.raises(ValueError, match="Duplicate aerial"):
        build([], [event, event])


@pytest.mark.parametrize("field", ["provider", "external_match_id"])
def test_rejects_aerial_identity_mismatch_outside_selected_period(
    field: str,
) -> None:
    with pytest.raises(ValueError, match="requested provider and match"):
        build(
            [],
            [aerial(1, period=2, **{field: "other"})],
            periods=(1,),
        )


def test_rejects_player_team_conflict_between_views() -> None:
    pressure = MatchPressure.model_validate(header(1, "Pressure"))

    with pytest.raises(ValueError, match="multiple teams"):
        build([pressure], [aerial(2, external_team_id="771")])


def test_rejects_invalid_source_hash() -> None:
    with pytest.raises(ValidationError):
        build_defensive_summary(
            [],
            [],
            provider="statsbomb",
            match_id="3869685",
            events_sha256="invalid",
            available_periods=(1, 2),
        )


def test_empty_scope_does_not_invent_players_or_teams() -> None:
    result = build([], [])

    assert result.players == ()
    assert result.teams == ()
    assert result.external_event_ids == ()
