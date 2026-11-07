import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.participation_events import (
    normalize_participation_events,
)


def event(
    event_type: str = "Substitution",
    **changes: object,
) -> dict[str, object]:
    result: dict[str, object] = {
        "type": {"name": event_type},
        "index": 100,
        "period": 1,
        "timestamp": "00:30:00.000",
        "team": {"id": 771},
        "player": {"id": 10},
    }

    if event_type == "Substitution":
        result["substitution"] = {"replacement": {"id": 20}}

    result.update(changes)

    return result


def test_substitution_generates_exit_and_entry() -> None:
    actions = normalize_participation_events([event()])

    assert [(item.external_player_id, item.action) for item in actions] == [
        ("10", "leave"),
        ("20", "enter"),
    ]

    assert all(item.external_team_id == "771" for item in actions)
    assert all(item.event_index == 100 for item in actions)
    assert all(item.period == 1 for item in actions)
    assert all(item.timestamp == "00:30:00.000" for item in actions)


def test_starting_xi_generates_eleven_entries() -> None:
    payload = [
        event(
            "Starting XI",
            player=None,
            timestamp="00:00:00.000",
            tactics={"lineup": [{"player": {"id": player_id}} for player_id in range(1, 12)]},
        )
    ]

    actions = normalize_participation_events(payload)

    assert len(actions) == 11
    assert {item.external_player_id for item in actions} == {
        str(player_id) for player_id in range(1, 12)
    }
    assert all(item.action == "enter" for item in actions)


@pytest.mark.parametrize(
    ("event_type", "action"),
    [
        ("Player Off", "leave"),
        ("Player On", "enter"),
    ],
)
def test_temporary_exit_and_return(
    event_type: str,
    action: str,
) -> None:
    actions = normalize_participation_events([event(event_type)])

    assert len(actions) == 1
    assert actions[0].action == action
    assert actions[0].external_player_id == "10"


def test_yellow_card_does_not_change_participation() -> None:
    actions = normalize_participation_events(
        [
            event(
                "Bad Behaviour",
                bad_behaviour={"card": {"name": "Yellow Card"}},
            )
        ]
    )

    assert actions == []


@pytest.mark.parametrize("card_name", ["Red Card", "Second Yellow"])
def test_dismissal_generates_exit(card_name: str) -> None:
    actions = normalize_participation_events(
        [
            event(
                "Foul Committed",
                foul_committed={"card": {"name": card_name}},
            )
        ]
    )

    assert len(actions) == 1
    assert actions[0].external_player_id == "10"
    assert actions[0].action == "leave"
    assert actions[0].reason == card_name


def test_penalty_shootout_does_not_change_playing_intervals() -> None:
    assert normalize_participation_events([event(period=5)]) == []


def test_unknown_card_category_is_rejected() -> None:
    with pytest.raises(ProviderUnavailable, match="Unsupported card"):
        normalize_participation_events(
            [
                event(
                    "Foul Committed",
                    foul_committed={"card": {"name": "Unknown Card"}},
                )
            ]
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"player": None},
        {"substitution": None},
        {"team": None},
    ],
)
def test_incomplete_substitution_is_rejected(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ProviderUnavailable):
        normalize_participation_events([event(**changes)])


def test_player_cannot_replace_himself() -> None:
    with pytest.raises(ProviderUnavailable, match="himself"):
        normalize_participation_events([event(substitution={"replacement": {"id": 10}})])


def test_empty_events_return_no_actions() -> None:
    assert normalize_participation_events([]) == []
