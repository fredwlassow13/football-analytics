import pytest

from football_analytics.integrations.statsbomb.period_clock import (
    PeriodBoundary,
    event_to_elapsed_ms,
    normalize_period_clock,
)


@pytest.fixture
def periods() -> list[PeriodBoundary]:
    endings = [
        (1, "00:52:34.569"),
        (2, "00:53:37.921"),
        (3, "00:16:04.648"),
        (4, "00:19:07.472"),
    ]

    return normalize_period_clock(
        [
            {
                "type": {"name": "Half End"},
                "period": period,
                "timestamp": timestamp,
            }
            for period, timestamp in endings
        ]
    )


@pytest.mark.parametrize(
    ("period", "timestamp", "expected"),
    [
        (1, "00:00:00.000", 0),
        (1, "00:28:11.248", 1_691_248),
        (1, "00:28:19.983", 1_699_983),
        (2, "00:18:51.930", 4_286_499),
        (3, "00:00:00.000", 6_372_490),
        (4, "00:19:07.472", 8_484_610),
    ],
)
def test_converts_event_time(
    periods: list[PeriodBoundary],
    period: int,
    timestamp: str,
    expected: int,
) -> None:
    assert event_to_elapsed_ms(period, timestamp, periods) == expected


@pytest.mark.parametrize("period", [0, 5, 6])
def test_rejects_nonplaying_period(
    periods: list[PeriodBoundary],
    period: int,
) -> None:
    with pytest.raises(ValueError, match="playing period"):
        event_to_elapsed_ms(period, "00:00:00.000", periods)


def test_rejects_time_after_period_end(
    periods: list[PeriodBoundary],
) -> None:
    with pytest.raises(ValueError, match="exceeds"):
        event_to_elapsed_ms(2, "00:54:00.000", periods)


def test_rejects_invalid_timestamp(
    periods: list[PeriodBoundary],
) -> None:
    with pytest.raises(ValueError, match="Invalid StatsBomb timestamp"):
        event_to_elapsed_ms(1, "invalid", periods)
