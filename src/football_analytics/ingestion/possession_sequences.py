from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.integrations.base import MatchEventContext
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary

type PossessionIssueCode = Literal[
    "missing_possession_sequence",
    "missing_possession_team",
    "conflicting_possession_teams",
    "non_contiguous_label",
    "source_index_gap",
    "timestamp_regression",
    "timestamp_after_period_end",
    "period_order_regression",
]

CLOCK_ISSUE_CODES = {
    "timestamp_regression",
    "timestamp_after_period_end",
    "period_order_regression",
}


class PossessionSequenceIssue(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    code: PossessionIssueCode
    period: int = Field(ge=1, le=4)
    possession_sequence: int | None
    external_event_ids: tuple[str, ...]


class PossessionFragment(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    fragment_number: int = Field(gt=0)
    period: int = Field(ge=1, le=4)
    possession_sequence: int | None
    external_possession_team_id: str | None
    observed_possession_team_ids: tuple[str, ...]
    first_source_index: int = Field(gt=0)
    last_source_index: int = Field(gt=0)
    first_timestamp_ms: int = Field(ge=0)
    last_timestamp_ms: int = Field(ge=0)
    observed_span_ms: int | None = Field(ge=0)
    event_count: int = Field(gt=0)
    event_type_counts: dict[str, int]
    external_event_ids: tuple[str, ...]
    issues: tuple[PossessionIssueCode, ...]


class PossessionSequenceResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    external_match_id: str = Field(min_length=1)
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: Literal["1.0.0"] = "1.0.0"
    population_basis: Literal["source_ordered_events"] = "source_ordered_events"
    available_periods: tuple[int, ...]
    fragments: tuple[PossessionFragment, ...]
    issues: tuple[PossessionSequenceIssue, ...]
    external_event_ids: tuple[str, ...]
    excluded_shootout_event_ids: tuple[str, ...]


def _validate_clock(
    periods: Sequence[PeriodBoundary],
) -> dict[int, PeriodBoundary]:
    ordered = sorted(periods, key=lambda item: item.period)
    indexed = {period.period: period for period in ordered}

    if len(indexed) != len(ordered) or set(indexed) not in ({1, 2}, {1, 2, 3, 4}):
        raise ValueError("Clock must describe two or four complete periods")

    previous_end = 0
    for period in ordered:
        if (
            period.duration_ms <= 0
            or period.start_elapsed_ms != previous_end
            or period.end_elapsed_ms != period.start_elapsed_ms + period.duration_ms
        ):
            raise ValueError("Invalid or noncontiguous period boundaries")
        previous_end = period.end_elapsed_ms

    return indexed


def build_possession_sequences(
    events: Sequence[MatchEventContext],
    periods: Sequence[PeriodBoundary],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
) -> PossessionSequenceResult:
    clock = _validate_clock(periods)
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()

    for event in events:
        if not isinstance(event, MatchEventContext):
            raise ValueError("Possession reconstruction requires event context")

        if (event.provider, event.external_match_id) != (provider, match_id):
            raise ValueError("Events differ from requested provider and match")

        if event.external_event_id in seen_ids or event.source_index in seen_indexes:
            raise ValueError("Duplicate possession source event")

        if event.period not in {*clock, 5}:
            raise ValueError("Event period is incompatible with clock")

        seen_ids.add(event.external_event_id)
        seen_indexes.add(event.source_index)

    ordered = sorted(events, key=lambda item: item.source_index)
    playing = [event for event in ordered if event.period != 5]
    issues: list[PossessionSequenceIssue] = []
    groups: list[list[MatchEventContext]] = []

    def record(
        code: PossessionIssueCode,
        current: MatchEventContext,
        affected: Sequence[MatchEventContext],
    ) -> None:
        issues.append(
            PossessionSequenceIssue(
                code=code,
                period=current.period,
                possession_sequence=current.possession_sequence,
                external_event_ids=tuple(item.external_event_id for item in affected),
            )
        )

    previous: MatchEventContext | None = None

    for current in playing:
        if current.possession_sequence is None:
            record("missing_possession_sequence", current, [current])

        if current.external_possession_team_id is None:
            record("missing_possession_team", current, [current])

        if current.timestamp_ms > clock[current.period].duration_ms:
            record("timestamp_after_period_end", current, [current])

        if previous is None:
            if current.source_index != 1:
                record("source_index_gap", current, [current])
        else:
            if current.source_index != previous.source_index + 1:
                record("source_index_gap", current, [previous, current])

            if current.period < previous.period:
                record("period_order_regression", current, [previous, current])

            if current.period == previous.period and current.timestamp_ms < previous.timestamp_ms:
                record("timestamp_regression", current, [previous, current])

        if (
            previous is None
            or current.period != previous.period
            or current.possession_sequence != previous.possession_sequence
            or current.source_index != previous.source_index + 1
        ):
            groups.append([])

        groups[-1].append(current)
        previous = current

    labels: dict[tuple[int, int], list[MatchEventContext]] = defaultdict(list)
    label_fragments: Counter[tuple[int, int]] = Counter()

    for group in groups:
        first = group[0]
        if first.possession_sequence is not None:
            key = (first.period, first.possession_sequence)
            labels[key].extend(group)
            label_fragments[key] += 1

    conflicting_labels: set[tuple[int, int]] = set()

    for key, label_events in labels.items():
        if label_fragments[key] > 1:
            record("non_contiguous_label", label_events[0], label_events)

        label_owners = {
            event.external_possession_team_id
            for event in label_events
            if event.external_possession_team_id is not None
        }
        if len(label_owners) > 1:
            conflicting_labels.add(key)
            record("conflicting_possession_teams", label_events[0], label_events)

    issues_by_event: dict[str, set[PossessionIssueCode]] = defaultdict(set)
    for issue in issues:
        for event_id in issue.external_event_ids:
            issues_by_event[event_id].add(issue.code)

    fragments: list[PossessionFragment] = []

    for number, group in enumerate(groups, start=1):
        first = group[0]
        last = group[-1]
        owners = tuple(
            sorted(
                {
                    event.external_possession_team_id
                    for event in group
                    if event.external_possession_team_id is not None
                }
            )
        )
        codes: set[PossessionIssueCode] = set()
        for event in group:
            codes.update(issues_by_event[event.external_event_id])

        owner: str | None = None
        if (
            first.possession_sequence is not None
            and len(owners) == 1
            and all(event.external_possession_team_id is not None for event in group)
            and (first.period, first.possession_sequence) not in conflicting_labels
        ):
            owner = owners[0]

        temporal_review = any(code in CLOCK_ISSUE_CODES for code in codes)

        fragments.append(
            PossessionFragment(
                fragment_number=number,
                period=first.period,
                possession_sequence=first.possession_sequence,
                external_possession_team_id=owner,
                observed_possession_team_ids=owners,
                first_source_index=first.source_index,
                last_source_index=last.source_index,
                first_timestamp_ms=first.timestamp_ms,
                last_timestamp_ms=last.timestamp_ms,
                observed_span_ms=(
                    None if temporal_review else last.timestamp_ms - first.timestamp_ms
                ),
                event_count=len(group),
                event_type_counts=dict(
                    sorted(Counter(event.event_type for event in group).items())
                ),
                external_event_ids=tuple(event.external_event_id for event in group),
                issues=tuple(sorted(codes)),
            )
        )

    return PossessionSequenceResult(
        provider=provider,
        external_match_id=match_id,
        events_sha256=events_sha256,
        available_periods=tuple(sorted(clock)),
        fragments=tuple(fragments),
        issues=tuple(issues),
        external_event_ids=tuple(event.external_event_id for event in playing),
        excluded_shootout_event_ids=tuple(
            event.external_event_id for event in ordered if event.period == 5
        ),
    )
