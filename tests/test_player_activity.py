import pytest

from football_analytics.ingestion.player_activity import select_player_activity
from football_analytics.integrations.base import MatchEventSpatial


def make_event(
    index: int,
    *,
    event_type: str = "Pass",
    period: int = 1,
    player_id: str = "player-1",
    x: float = 40.0,
    missing_location: bool = False,
) -> MatchEventSpatial:
    return MatchEventSpatial.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_event_id": f"event-{index}",
            "source_index": index,
            "period": period,
            "timestamp_ms": index * 1000,
            "event_type": event_type,
            "external_team_id": "team-1",
            "external_player_id": player_id,
            "coordinate_system": "statsbomb_120_80",
            "start_location": None if missing_location else {"x": x, "y": 30.0},
        }
    )


def select(events: list[MatchEventSpatial]):
    return select_player_activity(
        events,
        provider="statsbomb",
        match_id="3869685",
        team_id="team-1",
        player_id="player-1",
    )


@pytest.mark.parametrize("event_type", ["Pass", "Pressure", "Duel"])
def test_includes_on_ball_defensive_and_contested_events(event_type: str) -> None:
    result = select([make_event(1, event_type=event_type)])

    assert result.selected_count == 1
    assert result.events[0].event_type == event_type
    assert result.events[0].external_event_id == "event-1"


def test_preserves_sequence_without_merging_actions() -> None:
    result = select(
        [
            make_event(3, event_type="Pass"),
            make_event(1, event_type="Ball Receipt*"),
            make_event(2, event_type="Carry"),
        ]
    )

    assert [event.external_event_id for event in result.events] == ["event-1", "event-2", "event-3"]
    assert result.selected_count == 3


def test_excludes_administrative_events_with_audit_ids() -> None:
    result = select(
        [
            make_event(1),
            make_event(2, event_type="Player Off", missing_location=True),
        ]
    )

    assert result.selected_count == 1
    assert result.excluded_event_ids == ("event-2",)
    assert result.location_issues == ()


@pytest.mark.parametrize(
    ("missing", "x", "reason"),
    [
        (True, 40.0, "missing_location"),
        (False, 120.1, "outside_pitch"),
    ],
)
def test_records_unusable_locations(
    missing: bool,
    x: float,
    reason: str,
) -> None:
    result = select(
        [
            make_event(1, missing_location=missing, x=x),
        ]
    )

    assert result.selected_count == 0
    assert result.candidate_count == 1
    assert result.location_issues[0].external_event_id == "event-1"
    assert result.location_issues[0].reason == reason


def test_filters_other_players_and_shootout() -> None:
    result = select(
        [
            make_event(1, period=4),
            make_event(2, period=5),
            make_event(3, player_id="player-2"),
        ]
    )

    assert result.selected_count == 1
    assert result.events[0].external_event_id == "event-1"


def test_rejects_duplicate_source_events() -> None:
    event = make_event(1)
    with pytest.raises(ValueError, match="Duplicate"):
        select([event, event])


def test_requires_review_for_unknown_event_type() -> None:
    with pytest.raises(ValueError, match="Unclassified"):
        select([make_event(1, event_type="New Provider Event")])


def test_accepts_empty_input() -> None:
    result = select([])

    assert result.selected_count == 0
    assert result.candidate_count == 0
    assert result.excluded_event_ids == ()
