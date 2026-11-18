import pytest
from test_match_end_presence import player_for, transition

from football_analytics.ingestion.shootout_inputs import (
    prepare_shootout_inputs,
)
from football_analytics.integrations.statsbomb.participation_events import (
    PlayerAction,
)
from football_analytics.integrations.statsbomb.period_clock import (
    PeriodBoundary,
)


def action(index: int, kind: str, reason: str, **changes):
    return PlayerAction.model_validate(
        {
            "external_player_id": "player-1",
            "external_team_id": "779",
            "action": kind,
            "reason": reason,
            "event_index": index,
            "period": 1 if kind == "enter" else 2,
            "timestamp": ("00:00:00.000" if kind == "enter" else "00:00:00.400"),
        }
        | changes
    )


def build(players, actions, periods=None):
    return prepare_shootout_inputs(
        players,
        actions,
        [
            PeriodBoundary(
                period=1,
                duration_ms=500,
                start_elapsed_ms=0,
                end_elapsed_ms=500,
            ),
            PeriodBoundary(
                period=2,
                duration_ms=500,
                start_elapsed_ms=500,
                end_elapsed_ms=1000,
            ),
        ]
        if periods is None
        else periods,
        match_id="3869685",
    )


def test_prepares_active_player_without_inventing_negative_flags() -> None:
    result = build(
        [player_for([transition(1, "enter", 0)])],
        [action(1, "enter", "Starting XI")],
    )[0]

    assert result.presence.status == "active"
    assert result.eligibility_context.last_exit_reason is None
    assert result.eligibility_context.sent_off is None
    assert result.eligibility_context.excluded_to_equalise is None


def test_preserves_substitution_reason() -> None:
    result = build(
        [
            player_for(
                [
                    transition(1, "enter", 0),
                    transition(2, "leave", 900),
                ]
            )
        ],
        [
            action(1, "enter", "Starting XI"),
            action(2, "leave", "Substitution"),
        ],
    )[0]

    assert result.presence.status == "inactive"
    assert result.eligibility_context.last_exit_reason == "Substitution"
    assert result.eligibility_context.sent_off is None


def test_recorded_red_card_is_positive_disciplinary_evidence() -> None:
    result = build(
        [
            player_for(
                [
                    transition(1, "enter", 0),
                    transition(2, "leave", 900),
                ]
            )
        ],
        [
            action(1, "enter", "Starting XI"),
            action(2, "leave", "Red Card"),
        ],
    )[0]

    assert result.eligibility_context.sent_off is True
    assert result.eligibility_context.last_exit_reason == "Red Card"


def test_player_without_transitions_keeps_unknown_presence() -> None:
    result = build([player_for([])], [])[0]

    assert result.presence.status == "unknown"
    assert result.eligibility_context.last_exit_reason is None


def test_rejects_unlisted_action() -> None:
    with pytest.raises(ValueError, match="unlisted player"):
        build([], [action(1, "enter", "Starting XI")])


def test_rejects_wrong_team() -> None:
    with pytest.raises(ValueError, match="Action team differs from participation"):
        build(
            [player_for([transition(1, "enter", 0)])],
            [
                action(
                    1,
                    "enter",
                    "Starting XI",
                    external_team_id="771",
                )
            ],
        )


def test_rejects_incomplete_clock() -> None:
    with pytest.raises(ValueError, match="Clock must describe two or four complete periods"):
        build([], [], periods=[])


def test_rejects_shootout_actions_as_match_participation() -> None:
    with pytest.raises(ValueError, match="outside playing periods"):
        build(
            [player_for([])],
            [action(1, "enter", "Player On", period=5)],
        )
