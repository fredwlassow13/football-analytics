import pytest
from pydantic import ValidationError

from football_analytics.ingestion.action_context import ContextualizedAction
from football_analytics.ingestion.pass_network_window import (
    PassNetworkWindow,
    select_pass_network_window,
)
from football_analytics.integrations.base import (
    MatchEventContext,
    MatchEventSpatial,
    MatchPass,
    MatchShot,
)


def make_window() -> PassNetworkWindow:
    return PassNetworkWindow(
        provider="statsbomb",
        external_match_id="3869685",
        label="Trecho de teste",
        start_source_index=100,
        end_source_index=200,
    )


def make_action(
    index: int,
    *,
    period: int = 1,
    is_shot: bool = False,
) -> ContextualizedAction:
    header = {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": f"event-{index}",
        "source_index": index,
        "period": period,
        "timestamp_ms": index * 1000,
        "event_type": "Shot" if is_shot else "Pass",
        "external_team_id": "team-1",
        "external_player_id": "player-1",
    }

    action = (
        MatchShot.model_validate(header | {"outcome": "goal"})
        if is_shot
        else MatchPass.model_validate(
            header
            | {
                "outcome": "complete",
                "external_recipient_player_id": "player-2",
            }
        )
    )

    return ContextualizedAction(
        action=action,
        context=MatchEventContext.model_validate(header),
        spatial=MatchEventSpatial.model_validate(
            header
            | {
                "coordinate_system": "statsbomb_120_80",
                "start_location": {"x": 50.0, "y": 30.0},
            }
        ),
    )


def test_includes_start_and_excludes_end_without_changing_data() -> None:
    first = make_action(100)
    last = make_action(199)

    selected = select_pass_network_window(
        [make_action(99), first, last, make_action(200)],
        make_window(),
    )

    assert [item.action.source_index for item in selected] == [100, 199]
    assert selected[0] is first
    assert selected[1] is last
    assert selected[0].spatial.start_location is not None
    assert selected[0].spatial.start_location.x == 50.0


def test_orders_selected_events_by_source_index() -> None:
    selected = select_pass_network_window(
        [make_action(150), make_action(100)],
        make_window(),
    )

    assert [item.action.source_index for item in selected] == [100, 150]


def test_excludes_other_actions_and_shootout() -> None:
    selected = select_pass_network_window(
        [
            make_action(100),
            make_action(101, is_shot=True),
            make_action(102, period=5),
        ],
        make_window(),
    )

    assert [item.action.source_index for item in selected] == [100]


@pytest.mark.parametrize("field", ["provider", "external_match_id"])
def test_rejects_window_from_another_source_or_match(field: str) -> None:
    data = make_window().model_dump()
    data[field] = "another"

    with pytest.raises(ValueError, match="share provider and match"):
        select_pass_network_window(
            [make_action(100)],
            PassNetworkWindow.model_validate(data),
        )


def test_accepts_empty_input() -> None:
    assert select_pass_network_window([], make_window()) == []


@pytest.mark.parametrize(
    ("start", "end"),
    [(0, 200), (100, 100), (200, 100)],
)
def test_rejects_invalid_window_bounds(start: int, end: int) -> None:
    data = make_window().model_dump()
    data.update(start_source_index=start, end_source_index=end)

    with pytest.raises(ValidationError):
        PassNetworkWindow.model_validate(data)
