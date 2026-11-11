import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.tactical_windows import (
    normalize_tactical_boundaries,
    normalize_tactical_windows,
)


def event(
    index: int,
    period: int,
    timestamp: str,
    event_type: str,
    **details: object,
) -> dict[str, object]:
    return {
        "id": f"event-{index}",
        "index": index,
        "period": period,
        "timestamp": timestamp,
        "type": {"name": event_type},
        **details,
    }


def period_payload(count: int = 2) -> list[dict[str, object]]:
    return [
        item
        for period in range(1, count + 1)
        for item in [
            event(
                (period - 1) * 10 + 1,
                period,
                "00:00:00.000",
                "Half Start",
            ),
            event(
                period * 10,
                period,
                "00:00:10.000",
                "Half End",
            ),
        ]
    ]


def substitution(index: int, timestamp: str) -> dict[str, object]:
    return event(
        index,
        1,
        timestamp,
        "Substitution",
        team={"id": 1},
        player={"id": 10},
        substitution={"replacement": {"id": 11}},
    )


@pytest.mark.parametrize("period_count", [2, 4])
def test_covers_playing_periods_and_excludes_shootout(period_count: int) -> None:
    payload = period_payload(period_count)
    payload.append(
        event(
            period_count * 10 + 1,
            5,
            "00:00:03.000",
            "Half End",
        )
    )

    windows = normalize_tactical_windows(payload, match_id=123)

    assert [window.period for window in windows] == list(range(1, period_count + 1))
    assert sum(window.duration_ms for window in windows) == period_count * 10_000
    assert windows[-1].end_source_index == period_count * 10 + 1
    assert all(window.external_match_id == "123" for window in windows)


def test_extracts_substitution_and_tactical_shift() -> None:
    payload = period_payload() + [
        substitution(5, "00:00:05.000"),
        event(7, 1, "00:00:07.000", "Tactical Shift"),
    ]

    windows = normalize_tactical_windows(payload, match_id=123)

    assert [window.duration_ms for window in windows] == [
        5000,
        2000,
        3000,
        10_000,
    ]
    assert windows[1].start_reasons == ("substitution",)
    assert windows[1].start_event_ids == ("event-5",)
    assert windows[2].start_reasons == ("tactical_shift",)


@pytest.mark.parametrize(
    ("card_name", "expected"),
    [
        ("Yellow Card", False),
        ("Red Card", True),
        ("Second Yellow", True),
    ],
)
def test_identifies_dismissal_boundaries(card_name: str, expected: bool) -> None:
    payload = period_payload() + [
        event(
            5,
            1,
            "00:00:05.000",
            "Foul Committed",
            team={"id": 1},
            player={"id": 10},
            foul_committed={"card": {"name": card_name}},
        )
    ]

    boundaries = normalize_tactical_boundaries(payload, match_id=123)
    dismissals = [boundary for boundary in boundaries if boundary.reason == "dismissal"]

    assert bool(dismissals) is expected
    if expected:
        assert dismissals[0].source_event_id == "event-5"


def test_temporary_absence_does_not_split_tactical_catalogue() -> None:
    payload = period_payload() + [
        event(
            4,
            1,
            "00:00:04.000",
            "Player Off",
            team={"id": 1},
            player={"id": 10},
        ),
        event(
            6,
            1,
            "00:00:06.000",
            "Player On",
            team={"id": 1},
            player={"id": 10},
        ),
    ]

    windows = normalize_tactical_windows(payload, match_id=123)

    assert len(windows) == 2
    assert sum(window.duration_ms for window in windows) == 20_000


def test_preserves_simultaneous_changes_in_source_order() -> None:
    payload = period_payload() + [
        substitution(5, "00:00:05.000"),
        event(6, 1, "00:00:05.000", "Tactical Shift"),
    ]

    windows = normalize_tactical_windows(payload, match_id=123)

    assert [window.duration_ms for window in windows] == [
        5000,
        0,
        5000,
        10_000,
    ]
    assert windows[1].start_source_index == 5
    assert windows[1].end_source_index == 6


def test_rejects_event_beyond_period_end() -> None:
    payload = period_payload() + [
        event(5, 1, "00:00:11.000", "Pass"),
    ]

    with pytest.raises(ProviderUnavailable, match="exceeds period duration"):
        normalize_tactical_windows(payload, match_id=123)
