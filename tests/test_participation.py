from typing import Literal

import pytest

from football_analytics.ingestion.participation import (
    ParticipationTransition,
    build_player_intervals,
)

Action = Literal["enter", "leave"]


def transitions(
    rows: list[tuple[Action, int, int]],
) -> list[ParticipationTransition]:
    return [
        ParticipationTransition(
            action=action,
            elapsed_ms=elapsed_ms,
            event_index=event_index,
        )
        for action, elapsed_ms, event_index in rows
    ]


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (
            [("enter", 0, 1)],
            [(0, 100_000)],
        ),
        (
            [("enter", 30_000, 2)],
            [(30_000, 100_000)],
        ),
        (
            [("enter", 0, 1), ("leave", 30_000, 2)],
            [(0, 30_000)],
        ),
        (
            [
                ("enter", 0, 1),
                ("leave", 30_000, 2),
                ("enter", 35_000, 3),
            ],
            [(0, 30_000), (35_000, 100_000)],
        ),
        (
            [],
            [],
        ),
    ],
)
def test_builds_intervals(
    rows: list[tuple[Action, int, int]],
    expected: list[tuple[int, int]],
) -> None:
    result = build_player_intervals(
        transitions(rows),
        match_duration_ms=100_000,
    )

    assert [(item.start_ms, item.end_ms) for item in result] == expected


def test_orders_by_original_event_index() -> None:
    result = build_player_intervals(
        transitions(
            [
                ("leave", 30_000, 2),
                ("enter", 0, 1),
            ]
        ),
        match_duration_ms=100_000,
    )

    assert len(result) == 1
    assert result[0].start_ms == 0
    assert result[0].end_ms == 30_000


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [("enter", 0, 1), ("enter", 10_000, 2)],
            "already active",
        ),
        (
            [("leave", 10_000, 1)],
            "not active",
        ),
        (
            [("enter", 20_000, 1), ("leave", 10_000, 2)],
            "backwards",
        ),
        (
            [("enter", 100_001, 1)],
            "after match end",
        ),
        (
            [("enter", 0, 1), ("leave", 10_000, 1)],
            "Repeated event index",
        ),
    ],
)
def test_rejects_invalid_transitions(
    rows: list[tuple[Action, int, int]],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_player_intervals(
            transitions(rows),
            match_duration_ms=100_000,
        )


@pytest.mark.parametrize("duration", [0, -1])
def test_rejects_invalid_match_duration(duration: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        build_player_intervals(
            [],
            match_duration_ms=duration,
        )


def test_messi_temporary_exit_excludes_absence() -> None:
    result = build_player_intervals(
        transitions(
            [
                ("enter", 0, 1),
                ("leave", 1_691_248, 865),
                ("enter", 1_699_983, 867),
            ]
        ),
        match_duration_ms=8_484_610,
    )

    assert [(item.start_ms, item.end_ms) for item in result] == [
        (0, 1_691_248),
        (1_699_983, 8_484_610),
    ]

    assert sum(item.duration_ms for item in result) == 8_475_875
