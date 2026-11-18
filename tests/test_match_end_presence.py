import pytest

from football_analytics.ingestion.match_end_presence import (
    classify_match_end_presence,
)
from football_analytics.ingestion.match_participation import (
    PlayerParticipation,
)
from football_analytics.ingestion.participation import (
    ParticipationInterval,
    ParticipationTransition,
    build_player_intervals,
)


def transition(
    index: int,
    action: str,
    elapsed_ms: int,
) -> ParticipationTransition:
    return ParticipationTransition.model_validate(
        {
            "event_index": index,
            "action": action,
            "elapsed_ms": elapsed_ms,
        }
    )


def player_for(
    transitions: list[ParticipationTransition],
) -> PlayerParticipation:
    intervals = build_player_intervals(transitions, 1000)

    return PlayerParticipation(
        external_match_id="3869685",
        external_team_id="779",
        external_player_id="player-1",
        is_starter=None,
        intervals=intervals,
        elapsed_on_field_ms=(
            sum(interval.duration_ms for interval in intervals) if intervals else None
        ),
    )


def classify(transitions: list[ParticipationTransition]):
    return classify_match_end_presence(
        player_for(transitions),
        transitions,
        match_duration_ms=1000,
    )


def test_open_participation_is_active_at_match_end() -> None:
    result = classify([transition(1, "enter", 0)])

    assert result.status == "active"
    assert result.last_transition_index == 1


def test_recorded_exit_at_final_timestamp_is_inactive() -> None:
    result = classify(
        [
            transition(1, "enter", 0),
            transition(2, "leave", 1000),
        ]
    )

    assert result.status == "inactive"
    assert result.last_transition_index == 2


def test_no_transitions_means_unknown() -> None:
    result = classify([])

    assert result.status == "unknown"
    assert result.last_transition_index is None


def test_return_after_temporary_exit_is_active() -> None:
    result = classify(
        [
            transition(1, "enter", 0),
            transition(2, "leave", 400),
            transition(3, "enter", 600),
        ]
    )

    assert result.status == "active"


def test_uses_source_order_instead_of_input_order() -> None:
    result = classify(
        [
            transition(2, "leave", 900),
            transition(1, "enter", 0),
        ]
    )

    assert result.status == "inactive"
    assert result.last_transition_index == 2


def test_rejects_intervals_without_corresponding_transitions() -> None:
    player = player_for([])
    player.intervals = [ParticipationInterval(start_ms=0, end_ms=1000)]
    player.elapsed_on_field_ms = 1000

    with pytest.raises(ValueError, match="Transitions differ"):
        classify_match_end_presence(
            player,
            [],
            match_duration_ms=1000,
        )


def test_rejects_inconsistent_duration() -> None:
    transitions = [transition(1, "enter", 0)]
    player = player_for(transitions)
    player.elapsed_on_field_ms = 999

    with pytest.raises(ValueError, match="duration differs"):
        classify_match_end_presence(
            player,
            transitions,
            match_duration_ms=1000,
        )
