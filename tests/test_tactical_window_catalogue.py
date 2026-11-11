import pytest
from pydantic import ValidationError

from football_analytics.ingestion.tactical_window_catalogue import (
    BoundaryReason,
    TacticalBoundary,
    build_tactical_window_catalogue,
)


def boundary(
    period: int,
    elapsed_ms: int,
    source_index: int,
    reason: BoundaryReason,
    event_id: str | None = None,
) -> TacticalBoundary:
    return TacticalBoundary(
        period=period,
        elapsed_ms=elapsed_ms,
        source_index=source_index,
        reason=reason,
        source_event_id=event_id,
    )


def normal_periods() -> list[TacticalBoundary]:
    return [
        boundary(1, 0, 1, "period_start"),
        boundary(1, 10_000, 11, "period_end"),
        boundary(2, 10_000, 11, "period_start"),
        boundary(2, 20_000, 21, "period_end"),
    ]


def catalogue(boundaries: list[TacticalBoundary]):
    return build_tactical_window_catalogue(
        boundaries,
        provider="statsbomb",
        external_match_id="match-1",
    )


def test_covers_both_periods_without_manual_cuts() -> None:
    windows = catalogue(normal_periods())

    assert [window.period for window in windows] == [1, 2]
    assert [(window.start_source_index, window.end_source_index) for window in windows] == [
        (1, 11),
        (11, 21),
    ]
    assert sum(window.duration_ms for window in windows) == 20_000


def test_splits_at_substitution_and_tactical_change() -> None:
    windows = catalogue(
        normal_periods()
        + [
            boundary(1, 5000, 5, "substitution", "sub-1"),
            boundary(1, 8000, 8, "tactical_shift", "tactics-1"),
        ]
    )

    assert [window.duration_ms for window in windows] == [
        5000,
        3000,
        2000,
        10_000,
    ]
    assert windows[1].start_reasons == ("substitution",)
    assert windows[1].start_event_ids == ("sub-1",)
    assert windows[2].start_reasons == ("tactical_shift",)
    assert sum(window.duration_ms for window in windows) == 20_000


def test_preserves_source_order_for_simultaneous_changes() -> None:
    windows = catalogue(
        normal_periods()
        + [
            boundary(1, 5000, 5, "substitution", "sub-1"),
            boundary(1, 5000, 6, "tactical_shift", "tactics-1"),
        ]
    )

    assert [window.duration_ms for window in windows] == [
        5000,
        0,
        5000,
        10_000,
    ]
    assert windows[1].start_source_index == 5
    assert windows[1].end_source_index == 6
    assert sum(window.duration_ms for window in windows) == 20_000


def test_rejects_conflicting_clocks_at_same_source_index() -> None:
    with pytest.raises(ValueError, match="Conflicting clocks"):
        catalogue(
            normal_periods()
            + [
                boundary(1, 5000, 5, "substitution", "sub-1"),
                boundary(1, 6000, 5, "dismissal", "red-1"),
            ]
        )


def test_rejects_missing_period_end() -> None:
    with pytest.raises(ValueError, match="exactly one start and end"):
        catalogue(normal_periods()[:-1])


def test_rejects_gap_between_periods() -> None:
    boundaries = normal_periods()
    boundaries[2] = boundary(2, 11_000, 11, "period_start")

    with pytest.raises(ValueError, match="contiguous"):
        catalogue(boundaries)


def test_rejects_change_outside_period_clock() -> None:
    with pytest.raises(ValueError, match="outside period bounds"):
        catalogue(normal_periods() + [boundary(1, 11_000, 5, "substitution", "sub-1")])


def test_covers_extra_time_when_present() -> None:
    windows = catalogue(
        normal_periods()
        + [
            boundary(3, 20_000, 21, "period_start"),
            boundary(3, 30_000, 31, "period_end"),
            boundary(4, 30_000, 31, "period_start"),
            boundary(4, 40_000, 41, "period_end"),
        ]
    )

    assert [window.period for window in windows] == [1, 2, 3, 4]
    assert sum(window.duration_ms for window in windows) == 40_000


def test_rejects_incomplete_extra_time() -> None:
    with pytest.raises(ValueError, match="two periods or four"):
        catalogue(
            normal_periods()
            + [
                boundary(3, 20_000, 21, "period_start"),
                boundary(3, 30_000, 31, "period_end"),
            ]
        )


def test_requires_provenance_for_tactical_change() -> None:
    with pytest.raises(ValidationError, match="source event ID"):
        boundary(1, 5000, 5, "substitution")
