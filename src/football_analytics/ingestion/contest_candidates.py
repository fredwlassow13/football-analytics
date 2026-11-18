"""Review candidates around Duel/50-50 events; no automatic contest durations."""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Literal

from pydantic import Field

from football_analytics.ingestion.possession_time import TimeModel
from football_analytics.ingestion.possession_views import PossessionViews
from football_analytics.integrations.base import MatchEventContext

CONTEST_TYPES = frozenset({"Duel", "50/50"})
# Proxies for review, not proof that controlled possession was established.
CONTROL_PROXIES = frozenset({"Pass", "Carry", "Dribble", "Ball Receipt*"})
STOP_PROXIES = frozenset({"Half End", "Offside", "Injury Stoppage"})


class EventAnnotation(TimeModel):
    event_type: str
    duel_type: str | None = None
    outcome: str | None = None
    related_event_ids: tuple[str, ...] | None = None
    source_duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class CandidateEvent(TimeModel):
    external_event_id: str
    source_index: int
    period: int
    timestamp_ms: int
    event_type: str
    external_team_id: str | None
    external_player_id: str | None
    external_possession_team_id: str | None
    possession_sequence: int | None
    duel_type: str | None = None
    outcome: str | None = None
    related_event_ids: tuple[str, ...] | None = None
    source_duration_seconds: float | None = None


def _event(event: MatchEventContext, annotation: EventAnnotation | None = None) -> CandidateEvent:
    header = event.model_dump(include=set(CandidateEvent.model_fields))
    details = annotation.model_dump(exclude={"event_type"}) if annotation else {}
    return CandidateEvent.model_validate(header | details)


def _control(event: MatchEventContext) -> bool:
    return event.event_type in CONTROL_PROXIES and event.external_team_id is not None


def _aligned_control(event: MatchEventContext) -> bool:
    return _control(event) and event.external_team_id == event.external_possession_team_id


class CandidateOverlap(TimeModel):
    start_ms: Decimal
    end_ms: Decimal
    category: Literal["team", "in_contest", "stopped", "unknown"]
    external_team_id: str | None


class ContestCandidate(TimeModel):
    candidate_id: str
    review_status: Literal["pending"] = "pending"
    seed: CandidateEvent
    preceding_control_proxy: CandidateEvent | None
    following_boundary_proxy: CandidateEvent | None
    suggested_start_ms: Decimal | None
    suggested_end_ms: Decimal | None
    unclipped_end_ms: Decimal | None
    known_stoppage_limit_ms: Decimal | None
    crosses_known_stoppage: bool
    following_possession_aligned_proxy: CandidateEvent | None
    control_proxies_before_aligned_boundary: tuple[CandidateEvent, ...]
    paired_perspective_candidate_ids: tuple[str, ...]
    context_events: tuple[CandidateEvent, ...]
    interval_overlaps: tuple[CandidateOverlap, ...]
    overlaps_known_time: bool
    overlapping_candidate_ids: tuple[str, ...]
    issues: tuple[str, ...]


class ContestCandidateReport(TimeModel):
    policy_version: Literal["contest_candidates_1.1.0"] = "contest_candidates_1.1.0"
    provider: str
    external_match_id: str
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    estimate_policy_version: str
    estimate_max_gap_ms: int
    periods: tuple[int, ...]
    max_window_ms: int
    context_radius: int
    candidates: tuple[ContestCandidate, ...]
    excluded_shootout_seed_ids: tuple[str, ...]
    out_of_scope_seed_ids: tuple[str, ...]
    accepted_contest_windows: tuple[()] = ()


def build_contest_candidates(
    contexts: Sequence[MatchEventContext],
    views: PossessionViews,
    *,
    events_sha256: str,
    max_window_ms: int = 5000,
    context_radius: int = 3,
    event_annotations: Mapping[str, EventAnnotation] | None = None,
) -> ContestCandidateReport:
    if (
        type(max_window_ms) is not int
        or max_window_ms <= 0
        or type(context_radius) is not int
        or context_radius < 0
    ):
        raise ValueError("Invalid candidate window or context radius")
    if events_sha256 != views.events_sha256:
        raise ValueError("Candidate source hash differs from possession views")
    ordered = sorted(contexts, key=lambda e: e.source_index)
    if len({e.external_event_id for e in ordered}) != len(ordered) or len(
        {e.source_index for e in ordered}
    ) != len(ordered):
        raise ValueError("Duplicate candidate source event")
    if any(
        (e.provider, e.external_match_id) != (views.provider, views.external_match_id)
        for e in ordered
    ):
        raise ValueError("Candidate event belongs to another source")
    annotations = {} if event_annotations is None else dict(event_annotations)
    catalogue = {e.external_event_id: e for e in ordered}
    for key, annotation in annotations.items():
        if key not in catalogue or annotation.event_type != catalogue[key].event_type:
            raise ValueError("Annotation differs from candidate source")

    def snapshot(event: MatchEventContext) -> CandidateEvent:
        return _event(event, annotations.get(event.external_event_id))

    intervals = {p: [i for i in views.intervals if i.period == p] for p in views.periods}
    by_period = {p: [e for e in ordered if e.period == p] for p in views.periods}
    teams = {t.external_team_id for t in views.team_possession}
    for event in ordered:
        if event.period not in {1, 2, 3, 4, 5}:
            raise ValueError("Invalid event period")
        if event.external_team_id not in {None, *teams}:
            raise ValueError("Unknown candidate team")
    for period, rows in intervals.items():
        cursor = Decimal(0)
        if not rows:
            raise ValueError("Missing view period")
        for interval in sorted(rows, key=lambda i: i.start_ms):
            if interval.start_ms != cursor or interval.end_ms <= interval.start_ms:
                raise ValueError("Invalid view partition")
            cursor = interval.end_ms
        if any(e.timestamp_ms > cursor for e in by_period[period]):
            raise ValueError("Candidate event outside period clock")
    result = []
    for period, events in by_period.items():
        for position, seed in enumerate(events):
            if seed.event_type not in CONTEST_TYPES:
                continue
            previous = next((e for e in reversed(events[:position]) if _control(e)), None)
            boundary = next(
                (e for e in events[position + 1 :] if _control(e) or e.event_type in STOP_PROXIES),
                None,
            )
            aligned = next(
                (
                    e
                    for e in events[position + 1 :]
                    if _aligned_control(e) or e.event_type in STOP_PROXIES
                ),
                None,
            )
            intermediate = tuple(
                snapshot(e)
                for e in events[position + 1 :]
                if _control(e) and (aligned is None or e.source_index < aligned.source_index)
            )
            issues: set[str] = {"boundaries_require_independent_review"}
            if intermediate:
                issues.add("alternative_control_proxies_exposed")
            if boundary is not None and _control(boundary) and not _aligned_control(boundary):
                issues.add("boundary_possession_team_disagreement")
            start: Decimal | None = None
            end: Decimal | None = None
            unclipped: Decimal | None = None
            stop_limit: Decimal | None = None
            crosses_stop = False
            overlaps = []
            if previous is None:
                issues.add("missing_preceding_control_proxy")
            if boundary is None:
                issues.add("missing_following_boundary_proxy")
            else:
                end_position = next(
                    i
                    for i, e in enumerate(events)
                    if e.external_event_id == boundary.external_event_id
                )
                span = events[position : end_position + 1]
                regression = any(
                    b.timestamp_ms < a.timestamp_ms for a, b in zip(span, span[1:], strict=False)
                )
                gap = any(
                    b.source_index != a.source_index + 1
                    for a, b in zip(span, span[1:], strict=False)
                )
                unclipped = Decimal(boundary.timestamp_ms)
                future_stops = [
                    i
                    for i in intervals[period]
                    if i.category == "stopped"
                    and i.end_ms > seed.timestamp_ms
                    and i.start_ms < boundary.timestamp_ms
                ]
                if future_stops:
                    stop_limit = min(i.start_ms for i in future_stops)
                    crosses_stop = True
                    issues.add("known_stoppage_limits_window")
                limited_end = min(unclipped, stop_limit) if stop_limit is not None else unclipped
                duration = limited_end - seed.timestamp_ms
                if regression:
                    issues.add("timestamp_regression")
                if gap:
                    issues.add("source_index_gap")
                if duration <= 0:
                    issues.add("nonpositive_candidate_window")
                elif duration > max_window_ms:
                    issues.add("candidate_window_exceeds_cap")
                elif not regression and not gap:
                    start, end = Decimal(seed.timestamp_ms), limited_end
                    for interval in intervals[period]:
                        left = max(Decimal(start), interval.start_ms)
                        right = min(Decimal(end), interval.end_ms)
                        if left < right:
                            overlaps.append(
                                CandidateOverlap(
                                    start_ms=left,
                                    end_ms=right,
                                    category=interval.category,
                                    external_team_id=interval.external_team_id,
                                )
                            )
                    if sum((o.end_ms - o.start_ms for o in overlaps), Decimal(0)) != duration:
                        raise ValueError("Candidate outside view coverage")
            known = any(o.category != "unknown" for o in overlaps)
            if known:
                issues.add("overlaps_known_time")
            nearby = events[max(0, position - context_radius) : position + context_radius + 1]
            if any(
                b.timestamp_ms < a.timestamp_ms for a, b in zip(nearby, nearby[1:], strict=False)
            ):
                issues.add("context_clock_regression")
            result.append(
                ContestCandidate(
                    candidate_id=f"{views.external_match_id}:{seed.external_event_id}",
                    seed=snapshot(seed),
                    preceding_control_proxy=snapshot(previous) if previous else None,
                    following_boundary_proxy=snapshot(boundary) if boundary else None,
                    unclipped_end_ms=unclipped,
                    known_stoppage_limit_ms=stop_limit,
                    crosses_known_stoppage=crosses_stop,
                    following_possession_aligned_proxy=snapshot(aligned) if aligned else None,
                    control_proxies_before_aligned_boundary=intermediate,
                    paired_perspective_candidate_ids=(),
                    suggested_start_ms=start,
                    suggested_end_ms=end,
                    context_events=tuple(snapshot(e) for e in nearby),
                    interval_overlaps=tuple(overlaps),
                    overlaps_known_time=known,
                    overlapping_candidate_ids=(),
                    issues=tuple(sorted(issues)),
                )
            )
    updated = []
    for candidate in result:
        shared = tuple(
            other.candidate_id
            for other in result
            if other is not candidate
            and other.seed.period == candidate.seed.period
            and candidate.suggested_start_ms is not None
            and candidate.suggested_end_ms is not None
            and other.suggested_start_ms is not None
            and other.suggested_end_ms is not None
            and candidate.suggested_start_ms < other.suggested_end_ms
            and other.suggested_start_ms < candidate.suggested_end_ms
        )
        paired = tuple(
            other.candidate_id
            for other in result
            if other is not candidate
            and candidate.seed.event_type == other.seed.event_type == "50/50"
            and candidate.seed.period == other.seed.period
            and candidate.seed.timestamp_ms == other.seed.timestamp_ms
            and candidate.seed.external_team_id is not None
            and other.seed.external_team_id is not None
            and candidate.seed.external_team_id != other.seed.external_team_id
            and other.seed.external_event_id in (candidate.seed.related_event_ids or ())
            and candidate.seed.external_event_id in (other.seed.related_event_ids or ())
        )
        updated.append(
            candidate.model_copy(
                update={
                    "overlapping_candidate_ids": shared,
                    "paired_perspective_candidate_ids": paired,
                    "issues": tuple(
                        sorted(
                            {*candidate.issues, *(("overlapping_candidates",) if shared else ())}
                        )
                    ),
                }
            )
        )
    return ContestCandidateReport(
        provider=views.provider,
        external_match_id=views.external_match_id,
        events_sha256=events_sha256,
        estimate_policy_version=views.estimate_policy_version,
        estimate_max_gap_ms=views.max_gap_ms,
        periods=views.periods,
        max_window_ms=max_window_ms,
        context_radius=context_radius,
        candidates=tuple(updated),
        excluded_shootout_seed_ids=tuple(
            e.external_event_id for e in ordered if e.period == 5 and e.event_type in CONTEST_TYPES
        ),
        out_of_scope_seed_ids=tuple(
            e.external_event_id
            for e in ordered
            if e.period != 5 and e.period not in views.periods and e.event_type in CONTEST_TYPES
        ),
    )
