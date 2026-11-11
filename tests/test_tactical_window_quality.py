import pytest

from football_analytics.ingestion.tactical_window_catalogue import TacticalWindow
from football_analytics.ingestion.tactical_window_quality import (
    audit_event_clocks,
    require_usable_event_clocks,
)
from football_analytics.integrations.base import MatchEvent
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary


def windows() -> list[TacticalWindow]:
    return [
        TacticalWindow.model_validate(
            {
                "provider": "statsbomb",
                "external_match_id": "match-1",
                "period": 1,
                "number_in_period": number,
                "start_elapsed_ms": start_ms,
                "end_elapsed_ms": end_ms,
                "start_source_index": start_index,
                "end_source_index": end_index,
                "start_reasons": (reason,),
                "start_event_ids": (),
            }
        )
        for number, start_ms, end_ms, start_index, end_index, reason in [
            (1, 0, 5000, 1, 50, "period_start"),
            (2, 5000, 10_000, 50, 101, "tactical_shift"),
        ]
    ]


def periods() -> list[PeriodBoundary]:
    return [
        PeriodBoundary(
            period=1,
            duration_ms=10_000,
            start_elapsed_ms=0,
            end_elapsed_ms=10_000,
        )
    ]


def event(
    index: int,
    timestamp_ms: int,
    *,
    event_type: str = "Ball Receipt*",
    period: int = 1,
) -> MatchEvent:
    return MatchEvent(
        provider="statsbomb",
        external_match_id="match-1",
        external_event_id=f"event-{index}",
        source_index=index,
        period=period,
        timestamp_ms=timestamp_ms,
        event_type=event_type,
    )


def test_reports_receipt_issue_without_changing_timestamp() -> None:
    receipt = event(90, 275)

    issues = audit_event_clocks([receipt], windows(), periods())

    assert len(issues) == 1
    assert issues[0].external_event_id == "event-90"
    assert issues[0].reported_elapsed_ms == 275
    assert issues[0].window_number == 2
    assert issues[0].window_start_elapsed_ms == 5000
    assert receipt.timestamp_ms == 275


def test_accepts_coherent_pass_clock() -> None:
    assert (
        audit_event_clocks(
            [event(89, 9800, event_type="Pass")],
            windows(),
            periods(),
        )
        == []
    )


def test_uses_source_order_at_shared_clock_boundary() -> None:
    assert (
        audit_event_clocks(
            [event(49, 5000), event(50, 5000)],
            windows(),
            periods(),
        )
        == []
    )


@pytest.mark.parametrize(
    ("event_type", "blocked"),
    [("Ball Receipt*", False), ("Pass", True)],
)
def test_pass_analysis_declares_required_event_type(
    event_type: str,
    blocked: bool,
) -> None:
    issues = audit_event_clocks(
        [event(90, 275, event_type=event_type)],
        windows(),
        periods(),
    )

    assert len(issues) == 1

    if blocked:
        with pytest.raises(ValueError, match="required events"):
            require_usable_event_clocks(issues, event_types={"Pass"})
    else:
        require_usable_event_clocks(issues, event_types={"Pass"})
        assert issues[0].event_type == "Ball Receipt*"


def test_receipt_analysis_is_blocked_by_receipt_clock_issue() -> None:
    issues = audit_event_clocks(
        [event(90, 275)],
        windows(),
        periods(),
    )

    with pytest.raises(ValueError, match="required events"):
        require_usable_event_clocks(issues, event_types={"Ball Receipt*"})


def test_excludes_shootout_from_playing_clock_audit() -> None:
    assert (
        audit_event_clocks(
            [event(101, 5000, period=5)],
            windows(),
            periods(),
        )
        == []
    )


def test_rejects_event_without_source_window() -> None:
    with pytest.raises(ValueError, match="exactly one source window"):
        audit_event_clocks(
            [event(101, 10_000)],
            windows(),
            periods(),
        )


def test_rejects_overlapping_source_windows() -> None:
    catalogue = windows()

    with pytest.raises(ValueError, match="exactly one source window"):
        audit_event_clocks(
            [event(10, 1000)],
            [*catalogue, catalogue[0]],
            periods(),
        )


def test_rejects_unknown_playing_period() -> None:
    with pytest.raises(ValueError, match="unknown playing period"):
        audit_event_clocks(
            [event(90, 275, period=2)],
            windows(),
            periods(),
        )
