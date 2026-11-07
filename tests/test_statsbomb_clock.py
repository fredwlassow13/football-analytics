import pytest

from football_analytics.integrations.statsbomb.clock import (
    format_duration_ms,
    timestamp_to_milliseconds,
)


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("00:00:00.000", 0),
        ("00:28:11.248", 1_691_248),
        ("00:52:34.569", 3_154_569),
        ("01:00:00.000", 3_600_000),
    ],
)
def test_converts_timestamp_to_milliseconds(
    timestamp: str,
    expected: int,
) -> None:
    assert timestamp_to_milliseconds(timestamp) == expected


@pytest.mark.parametrize(
    "timestamp",
    [
        "28:11.248",
        "00:60:00.000",
        "00:00:60.000",
        "-00:01:00.000",
        "00:01:00",
        "00:01:00.1234",
    ],
)
def test_rejects_invalid_timestamp(timestamp: str) -> None:
    with pytest.raises(
        ValueError,
        match="Invalid StatsBomb timestamp",
    ):
        timestamp_to_milliseconds(timestamp)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0 min 00,000 s"),
        (60_000, "1 min 00,000 s"),
        (8_484_610, "141 min 24,610 s"),
    ],
)
def test_formats_duration(value: int, expected: str) -> None:
    assert format_duration_ms(value) == expected


def test_negative_duration_is_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        format_duration_ms(-1)
