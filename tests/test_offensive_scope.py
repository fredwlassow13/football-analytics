import pytest

from football_analytics.ingestion.offensive_scope import select_offensive_periods
from football_analytics.integrations.base import MatchDribble, MatchPass, MatchShot


def make_event(
    index: int,
    *,
    period: int = 1,
    event_type: str = "Pass",
    match_id: str = "3869685",
):
    header = {
        "provider": "statsbomb",
        "external_match_id": match_id,
        "external_event_id": f"event-{index}",
        "source_index": index,
        "period": period,
        "timestamp_ms": index * 1000,
        "external_team_id": "779",
        "external_player_id": "5503",
    }
    if event_type == "Shot":
        return MatchShot.model_validate(header | {"outcome": "goal", "statsbomb_xg": 0.2})
    if event_type == "Dribble":
        return MatchDribble.model_validate(header | {"outcome": "complete"})
    return MatchPass.model_validate(header | {"outcome": "complete"})


@pytest.mark.parametrize(
    "periods",
    [(1,), (2,), (3,), (4,), (1, 2), (3, 4), (1, 2, 3, 4)],
)
def test_selects_standard_playing_scopes(periods: tuple[int, ...]) -> None:
    events = [make_event(period, period=period) for period in range(1, 6)]

    result = select_offensive_periods(
        list(reversed(events)),
        available_periods=(1, 2, 3, 4),
        periods=periods,
    )

    assert [event.period for event in result] == list(periods)


def test_defaults_to_all_available_playing_periods() -> None:
    result = select_offensive_periods(
        [make_event(1), make_event(2, period=2), make_event(3, period=5)],
        available_periods=(1, 2),
    )

    assert [event.period for event in result] == [1, 2]


def test_preserves_event_objects_and_shot_attributes() -> None:
    shot = make_event(1, event_type="Shot")

    result = select_offensive_periods(
        [shot],
        available_periods=(1, 2),
    )

    assert result[0] is shot
    assert isinstance(result[0], MatchShot)
    assert result[0].statsbomb_xg == 0.2
    assert result[0].outcome == "goal"


def test_accepts_different_offensive_event_types() -> None:
    result = select_offensive_periods(
        [
            make_event(3, event_type="Dribble"),
            make_event(1),
            make_event(2, event_type="Shot"),
        ],
        available_periods=(1, 2),
    )

    assert [event.event_type for event in result] == ["Pass", "Shot", "Dribble"]


@pytest.mark.parametrize("periods", [(), (1, 1), (True,), (3,), (5,)])
def test_rejects_invalid_or_unavailable_requested_periods(
    periods: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match="Requested periods"):
        select_offensive_periods(
            [],
            available_periods=(1, 2),
            periods=periods,
        )


@pytest.mark.parametrize("available", [(1,), (1, 2, 3), (1, 1, 2), (True, 2)])
def test_rejects_invalid_match_periods(available: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="Available periods"):
        select_offensive_periods([], available_periods=available)


def test_rejects_duplicates_even_outside_requested_scope() -> None:
    event = make_event(1, period=2)

    with pytest.raises(ValueError, match="Duplicate"):
        select_offensive_periods(
            [event, event],
            available_periods=(1, 2),
            periods=(1,),
        )


def test_rejects_mixed_matches() -> None:
    with pytest.raises(ValueError, match="one provider and match"):
        select_offensive_periods(
            [make_event(1), make_event(2, match_id="another-match")],
            available_periods=(1, 2),
        )


def test_rejects_event_from_nonexistent_extra_time() -> None:
    with pytest.raises(ValueError, match="incompatible"):
        select_offensive_periods(
            [make_event(1, period=3)],
            available_periods=(1, 2),
        )


def test_accepts_empty_sample_in_valid_scope() -> None:
    assert (
        select_offensive_periods(
            [],
            available_periods=(1, 2),
            periods=(2,),
        )
        == []
    )
