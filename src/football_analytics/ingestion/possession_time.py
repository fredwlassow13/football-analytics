"""Conservative event-gap estimate; never official possession or ball-in-play time."""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.attack_sequences import CONTROL_TYPES, AttackSequenceResult
from football_analytics.ingestion.possession_sequences import _validate_clock
from football_analytics.integrations.base import MatchEventContext
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary


class TimeModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class PossessionTimeInterval(TimeModel):
    period: int
    start_ms: Decimal
    end_ms: Decimal
    classification: Literal["attributed", "stopped", "unknown"]
    external_team_id: str | None
    evidence_event_ids: tuple[str, ...]
    reason: str


class TeamPossessionTime(TimeModel):
    external_team_id: str
    estimated_ms: Decimal
    share_of_attributed_pct: Decimal | None


class PossessionTimeResult(TimeModel):
    policy_version: Literal["event_gap_estimate_1.0.0"] = "event_gap_estimate_1.0.0"
    provider: str
    external_match_id: str
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    max_gap_ms: int
    periods: tuple[int, ...]
    elapsed_ms: Decimal
    attributed_ms: Decimal
    bounded_stoppage_ms: Decimal
    unknown_ms: Decimal
    attribution_coverage_pct: Decimal
    teams: tuple[TeamPossessionTime, ...]
    intervals: tuple[PossessionTimeInterval, ...]


def _pct(value: Decimal, denominator: Decimal) -> Decimal | None:
    if not denominator:
        return None
    return (value * 100 / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def estimate_possession_time(
    contexts: Sequence[MatchEventContext],
    attacks: AttackSequenceResult,
    periods: Sequence[PeriodBoundary],
    *,
    team_ids: tuple[str, str],
    max_gap_ms: int = 5000,
    selected_periods: tuple[int, ...] | None = None,
) -> PossessionTimeResult:
    """Partition elapsed time into estimated ownership, bounded stops and unknown.

    Only adjacent source events that are both control anchors in the same
    bounded attack can support attribution. The gap cap is a declared assumption.
    A stoppage takes precedence; conflicting ownership remains unknown.
    """
    clock = _validate_clock(periods)
    if len(team_ids) != 2 or len(set(team_ids)) != 2 or not all(team_ids):
        raise ValueError("Two distinct teams required")
    if type(max_gap_ms) is not int or max_gap_ms <= 0:
        raise ValueError("Gap cap must be a positive integer")
    selection = tuple(sorted(clock)) if selected_periods is None else selected_periods
    if (
        not selection
        or len(set(selection)) != len(selection)
        or any(type(p) is not int or p not in clock for p in selection)
    ):
        raise ValueError("Invalid period selection")
    if tuple(sorted(clock)) != attacks.available_periods:
        raise ValueError("Attack clock coverage differs")
    ordered = sorted(contexts, key=lambda e: e.source_index)
    if len({e.external_event_id for e in ordered}) != len(ordered) or len(
        {e.source_index for e in ordered}
    ) != len(ordered):
        raise ValueError("Duplicate source events")
    for e in ordered:
        if (e.provider, e.external_match_id) != (attacks.provider, attacks.external_match_id):
            raise ValueError("Event belongs to another source")
        if e.period not in {*clock, 5}:
            raise ValueError("Event outside clock periods")
        if e.external_possession_team_id not in {None, *team_ids}:
            raise ValueError("Unknown possession team")
        if e.external_team_id not in {None, *team_ids}:
            raise ValueError("Unknown event team")
    playing = [e for e in ordered if e.period != 5]
    if tuple(e.external_event_id for e in playing) != attacks.external_event_ids:
        raise ValueError("Attack and event coverage/order differs")
    if tuple(e.external_event_id for e in ordered if e.period == 5) != (
        attacks.excluded_shootout_event_ids
    ):
        raise ValueError("Shootout coverage differs")
    by_id = {e.external_event_id: e for e in playing}
    # Reject stale or mismatched fragment headers before assigning any durations.
    for fragment in attacks.fragments:
        group = [by_id[key] for key in fragment.external_event_ids]
        if (
            not group
            or len(group) != fragment.event_count
            or group[0].source_index != fragment.first_source_index
            or group[-1].source_index != fragment.last_source_index
            or group[0].timestamp_ms != fragment.first_timestamp_ms
            or group[-1].timestamp_ms != fragment.last_timestamp_ms
            or any(
                e.period != fragment.period or e.possession_sequence != fragment.possession_sequence
                for e in group
            )
            or tuple(
                sorted(
                    {
                        e.external_possession_team_id
                        for e in group
                        if e.external_possession_team_id is not None
                    }
                )
            )
            != fragment.observed_possession_team_ids
        ):
            raise ValueError("Attack fragment differs from event context")
    # Candidate intervals: (start, end, owner, evidence). None denotes a stop.
    candidates: dict[int, list[tuple[Decimal, Decimal, str | None, tuple[str, ...]]]] = {
        p: [] for p in selection
    }
    stops = {e.external_event_id for e in attacks.boundary_evidence if e.reason != "restart"}
    for attack in attacks.attacks:
        if attack.period not in candidates or attack.status != "bounded":
            continue
        team = attack.external_team_id
        if team not in team_ids:
            raise ValueError("Invalid bounded attack team")
        ids = set(attack.control_event_ids)
        group = [by_id[key] for key in attack.external_event_ids]
        for left, right in zip(group, group[1:], strict=False):
            gap = right.timestamp_ms - left.timestamp_ms
            if (
                left.external_event_id in ids
                and right.external_event_id in ids
                and left.external_event_id not in stops
                and left.event_type in CONTROL_TYPES
                and right.event_type in CONTROL_TYPES
                and left.external_team_id == right.external_team_id == team
                and left.external_possession_team_id == right.external_possession_team_id == team
                and left.possession_sequence is not None
                and left.possession_sequence == right.possession_sequence
                and left.period == right.period == attack.period
                and right.source_index == left.source_index + 1
                and 0 < gap <= max_gap_ms
            ):
                candidates[left.period].append(
                    (
                        Decimal(left.timestamp_ms),
                        Decimal(right.timestamp_ms),
                        team,
                        (left.external_event_id, right.external_event_id),
                    )
                )
    for stop in attacks.stoppages:
        if stop.period not in candidates or stop.status != "bounded":
            continue
        start, stop_end = stop.start_timestamp_ms, stop.end_timestamp_ms
        if (
            start is None
            or stop_end is None
            or stop.duration_ms is None
            or stop.duration_ms != Decimal(stop_end) - start
        ):
            raise ValueError("Invalid bounded stoppage")
        if start < stop_end:
            evidence = (
                *stop.stop_event_ids,
                *((stop.restart_event_id,) if stop.restart_event_id else ()),
            )
            candidates[stop.period].append((start, Decimal(stop_end), None, evidence))
    intervals = []
    totals = dict.fromkeys(team_ids, Decimal(0))
    stopped = unknown = elapsed = Decimal(0)
    for period in sorted(selection):
        end = Decimal(clock[period].duration_ms)
        rows = candidates[period]
        if any(not (0 <= a < b <= end) for a, b, _, _ in rows):
            raise ValueError("Candidate interval outside period clock")
        edges = sorted({Decimal(0), end, *(v for row in rows for v in row[:2])})
        elapsed += end
        for a, b in zip(edges, edges[1:], strict=False):
            active = [row for row in rows if row[0] <= a and row[1] >= b]
            owners = {row[2] for row in active}
            evidence = tuple(dict.fromkeys(key for row in active for key in row[3]))
            owner = None
            classification: Literal["attributed", "stopped", "unknown"]
            if None in owners:
                classification, reason = "stopped", "bounded_stoppage"
                stopped += b - a
            elif len(owners) == 1:
                owner = next(iter(owners))
                assert owner is not None
                classification, reason = "attributed", "short_control_event_gap"
                totals[owner] += b - a
            else:
                classification = "unknown"
                reason = "conflicting_ownership" if owners else "insufficient_evidence"
                unknown += b - a
            intervals.append(
                PossessionTimeInterval(
                    period=period,
                    start_ms=a,
                    end_ms=b,
                    classification=classification,
                    external_team_id=owner,
                    evidence_event_ids=evidence,
                    reason=reason,
                )
            )
    attributed = sum(totals.values(), Decimal(0))
    assert attributed + stopped + unknown == elapsed
    return PossessionTimeResult(
        provider=attacks.provider,
        external_match_id=attacks.external_match_id,
        events_sha256=attacks.events_sha256,
        max_gap_ms=max_gap_ms,
        periods=tuple(sorted(selection)),
        elapsed_ms=elapsed,
        attributed_ms=attributed,
        bounded_stoppage_ms=stopped,
        unknown_ms=unknown,
        attribution_coverage_pct=_pct(attributed, elapsed) or Decimal(0),
        teams=tuple(
            TeamPossessionTime(
                external_team_id=t,
                estimated_ms=totals[t],
                share_of_attributed_pct=_pct(totals[t], attributed),
            )
            for t in team_ids
        ),
        intervals=tuple(intervals),
    )
