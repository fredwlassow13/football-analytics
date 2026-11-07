import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.period_clock import (
    normalize_period_clock,
)


def end_event(
    period: int,
    timestamp: str,
) -> dict[str, object]:
    return {
        "type": {"name": "Half End"},
        "period": period,
        "timestamp": timestamp,
    }


def test_deduplicates_period_ends() -> None:
    first = end_event(1, "00:45:00.000")
    second = end_event(2, "00:46:00.000")

    periods = normalize_period_clock([first, first, second, second])

    assert len(periods) == 2
    assert periods[0].start_elapsed_ms == 0
    assert periods[0].end_elapsed_ms == 2_700_000
    assert periods[1].start_elapsed_ms == 2_700_000
    assert periods[1].duration_ms == 2_760_000
    assert periods[1].end_elapsed_ms == 5_460_000


def test_accumulates_four_periods_with_precision() -> None:
    periods = normalize_period_clock(
        [
            end_event(1, "00:52:34.569"),
            end_event(2, "00:53:37.921"),
            end_event(3, "00:16:04.648"),
            end_event(4, "00:19:07.472"),
        ]
    )

    assert [item.period for item in periods] == [1, 2, 3, 4]
    assert [item.start_elapsed_ms for item in periods] == [
        0,
        3_154_569,
        6_372_490,
        7_337_138,
    ]
    assert periods[-1].end_elapsed_ms == 8_484_610


def test_conflicting_ends_are_rejected() -> None:
    payload = [
        end_event(1, "00:45:00.000"),
        end_event(1, "00:46:00.000"),
        end_event(2, "00:45:00.000"),
    ]

    with pytest.raises(ProviderUnavailable, match="Conflicting"):
        normalize_period_clock(payload)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [end_event(1, "00:45:00.000")],
        [end_event(2, "00:45:00.000")],
        [
            end_event(1, "00:45:00.000"),
            end_event(2, "00:45:00.000"),
            end_event(4, "00:15:00.000"),
        ],
    ],
)
def test_incomplete_period_sets_are_rejected(
    payload: object,
) -> None:
    with pytest.raises(ProviderUnavailable):
        normalize_period_clock(payload)


def test_invalid_end_timestamp_is_rejected() -> None:
    payload = [
        end_event(1, "invalid"),
        end_event(2, "00:45:00.000"),
    ]

    with pytest.raises(ProviderUnavailable, match="Invalid end timestamp"):
        normalize_period_clock(payload)


def test_zero_duration_is_rejected() -> None:
    payload = [
        end_event(1, "00:00:00.000"),
        end_event(2, "00:45:00.000"),
    ]

    with pytest.raises(ProviderUnavailable, match="Invalid duration"):
        normalize_period_clock(payload)


def test_penalty_shootout_is_excluded() -> None:
    payload = [
        end_event(1, "00:45:00.000"),
        end_event(2, "00:45:00.000"),
        end_event(3, "00:15:00.000"),
        end_event(4, "00:15:00.000"),
        end_event(5, "00:06:00.000"),
    ]

    periods = normalize_period_clock(payload)

    assert [item.period for item in periods] == [1, 2, 3, 4]
    assert periods[-1].end_elapsed_ms == 7_200_000
