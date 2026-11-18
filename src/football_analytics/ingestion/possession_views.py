"""Elapsed-time distribution and possession shares, with reviewed contest evidence."""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import Field, model_validator

from football_analytics.ingestion.possession_time import (
    PossessionTimeInterval,
    PossessionTimeResult,
    TimeModel,
)
from football_analytics.integrations.base import MatchEventContext


class ReviewedContestWindow(TimeModel):
    provider: str
    external_match_id: str
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    period: int = Field(ge=1, le=4)
    start_ms: Decimal = Field(ge=0, allow_inf_nan=False)
    end_ms: Decimal = Field(gt=0, allow_inf_nan=False)
    evidence_event_ids: tuple[str, ...] = Field(min_length=1)
    review_reference: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_window(self) -> "ReviewedContestWindow":
        if self.end_ms <= self.start_ms:
            raise ValueError("Contest window must have positive duration")
        if len(set(self.evidence_event_ids)) != len(self.evidence_event_ids):
            raise ValueError("Duplicate contest evidence")
        if not self.review_reference.strip():
            raise ValueError("Review reference required")
        return self


class DistributionRow(TimeModel):
    category: Literal["team", "in_contest", "stopped", "unknown"]
    external_team_id: str | None
    duration_ms: Decimal
    share_of_elapsed_pct: Decimal


class PossessionShare(TimeModel):
    external_team_id: str
    duration_ms: Decimal
    share_of_controlled_pct: Decimal | None


class ViewedInterval(TimeModel):
    period: int
    start_ms: Decimal
    end_ms: Decimal
    category: Literal["team", "in_contest", "stopped", "unknown"]
    external_team_id: str | None
    evidence_event_ids: tuple[str, ...]
    reason: str


class PossessionViews(TimeModel):
    view_version: Literal["1.0.0"] = "1.0.0"
    provider: str
    external_match_id: str
    events_sha256: str
    estimate_policy_version: str
    max_gap_ms: int
    periods: tuple[int, ...]
    elapsed_ms: Decimal
    controlled_ms: Decimal
    controlled_coverage_pct: Decimal
    classified_coverage_pct: Decimal
    contest_assessment: Literal["not_reviewed", "reviewed_windows_only"]
    time_distribution: tuple[DistributionRow, ...]
    team_possession: tuple[PossessionShare, ...]
    reviewed_contests: tuple[ReviewedContestWindow, ...]
    intervals: tuple[ViewedInterval, ...]


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal:
    return (numerator * 100 / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_possession_views(
    estimate: PossessionTimeResult,
    *,
    contests: Sequence[ReviewedContestWindow] = (),
    contexts: Sequence[MatchEventContext] = (),
) -> PossessionViews:
    """Reclassify only unknown time using reviewed windows; never infer a contest.

    A duel/50-50 event supports an instant, not a duration. The supplied windows
    require independent review of their boundaries, recorded in review_reference.
    """
    teams = tuple(t.external_team_id for t in estimate.teams)
    if len(teams) != 2 or len(set(teams)) != 2 or estimate.elapsed_ms <= 0:
        raise ValueError("Two teams and positive elapsed time required")
    source = (estimate.provider, estimate.external_match_id, estimate.events_sha256)
    by_period: dict[int, list[PossessionTimeInterval]] = {p: [] for p in estimate.periods}
    for interval in estimate.intervals:
        if interval.period not in by_period or interval.end_ms <= interval.start_ms:
            raise ValueError("Invalid estimate interval")
        by_period[interval.period].append(interval)
    elapsed = Decimal(0)
    durations = dict.fromkeys(teams, Decimal(0))
    stopped = unknown = Decimal(0)
    for intervals in by_period.values():
        cursor = Decimal(0)
        if not intervals:
            raise ValueError("Missing estimate period")
        for interval in sorted(intervals, key=lambda i: i.start_ms):
            if interval.start_ms != cursor:
                raise ValueError("Estimate intervals overlap or leave gaps")
            cursor = interval.end_ms
            duration = interval.end_ms - interval.start_ms
            if interval.classification == "attributed":
                if interval.external_team_id not in durations:
                    raise ValueError("Unknown attributed team")
                durations[interval.external_team_id] += duration
            elif interval.external_team_id is not None:
                raise ValueError("Non-possession interval has a team")
            elif interval.classification == "stopped":
                stopped += duration
            else:
                unknown += duration
        elapsed += cursor
    if (
        elapsed != estimate.elapsed_ms
        or stopped != estimate.bounded_stoppage_ms
        or unknown != estimate.unknown_ms
        or sum(durations.values(), Decimal(0)) != estimate.attributed_ms
        or any(t.estimated_ms != durations[t.external_team_id] for t in estimate.teams)
    ):
        raise ValueError("Estimate totals differ from interval partition")
    catalogue = {e.external_event_id: e for e in contexts}
    if len(catalogue) != len(contexts):
        raise ValueError("Duplicate context event")
    if any((e.provider, e.external_match_id) != source[:2] for e in contexts):
        raise ValueError("Context belongs to another source")
    windows = sorted(contests, key=lambda w: (w.period, w.start_ms, w.end_ms))
    previous: ReviewedContestWindow | None = None
    for window in windows:
        if (window.provider, window.external_match_id, window.events_sha256) != source:
            raise ValueError("Contest source/hash differs")
        if window.period not in by_period:
            raise ValueError("Contest outside selected periods")
        if (
            previous is not None
            and previous.period == window.period
            and (window.start_ms < previous.end_ms)
        ):
            raise ValueError("Contest windows overlap")
        previous = window
        covered = Decimal(0)
        for interval in by_period[window.period]:
            overlap = min(interval.end_ms, window.end_ms) - max(interval.start_ms, window.start_ms)
            if overlap > 0:
                if interval.classification != "unknown":
                    raise ValueError("Contest conflicts with possession or stoppage")
                covered += overlap
        if covered != window.end_ms - window.start_ms:
            raise ValueError("Contest outside unknown source coverage")
        evidence = []
        for key in window.evidence_event_ids:
            event = catalogue.get(key)
            if (
                event is None
                or event.period != window.period
                or not window.start_ms <= event.timestamp_ms <= window.end_ms
            ):
                raise ValueError("Contest evidence outside window/source")
            evidence.append(event)
        if not any(e.event_type in {"Duel", "50/50"} for e in evidence):
            raise ValueError("Contest requires duel or 50/50 evidence plus boundary review")
    output = []
    contested = Decimal(0)
    for interval in estimate.intervals:
        overlapping = [
            w
            for w in windows
            if w.period == interval.period
            and w.start_ms < interval.end_ms
            and w.end_ms > interval.start_ms
        ]
        edges = sorted(
            {
                interval.start_ms,
                interval.end_ms,
                *(max(interval.start_ms, w.start_ms) for w in overlapping),
                *(min(interval.end_ms, w.end_ms) for w in overlapping),
            }
        )
        for start, end in zip(edges, edges[1:], strict=False):
            contest = next(
                (w for w in overlapping if w.start_ms <= start and w.end_ms >= end), None
            )
            category: Literal["team", "in_contest", "stopped", "unknown"]
            if contest is not None:
                category = "in_contest"
                contested += end - start
            else:
                category = (
                    "team" if interval.classification == "attributed" else interval.classification
                )
            output.append(
                ViewedInterval(
                    period=interval.period,
                    start_ms=start,
                    end_ms=end,
                    category=category,
                    external_team_id=interval.external_team_id,
                    evidence_event_ids=(
                        contest.evidence_event_ids if contest else interval.evidence_event_ids
                    ),
                    reason="reviewed_contest" if contest else interval.reason,
                )
            )
    remaining_unknown = unknown - contested
    controlled = sum(durations.values(), Decimal(0))
    rows = [
        DistributionRow(
            category="team",
            external_team_id=t,
            duration_ms=durations[t],
            share_of_elapsed_pct=_pct(durations[t], elapsed),
        )
        for t in teams
    ]
    other_totals: tuple[tuple[Literal["in_contest", "stopped", "unknown"], Decimal], ...] = (
        ("in_contest", contested),
        ("stopped", stopped),
        ("unknown", remaining_unknown),
    )
    rows.extend(
        DistributionRow(
            category=category,
            external_team_id=None,
            duration_ms=duration,
            share_of_elapsed_pct=_pct(duration, elapsed),
        )
        for category, duration in other_totals
    )
    return PossessionViews(
        provider=estimate.provider,
        external_match_id=estimate.external_match_id,
        events_sha256=estimate.events_sha256,
        estimate_policy_version=estimate.policy_version,
        max_gap_ms=estimate.max_gap_ms,
        periods=estimate.periods,
        elapsed_ms=elapsed,
        controlled_ms=controlled,
        controlled_coverage_pct=_pct(controlled, elapsed),
        classified_coverage_pct=_pct(elapsed - remaining_unknown, elapsed),
        contest_assessment="reviewed_windows_only" if windows else "not_reviewed",
        time_distribution=tuple(rows),
        team_possession=tuple(
            PossessionShare(
                external_team_id=t,
                duration_ms=durations[t],
                share_of_controlled_pct=(_pct(durations[t], controlled) if controlled else None),
            )
            for t in teams
        ),
        reviewed_contests=tuple(windows),
        intervals=tuple(output),
    )
