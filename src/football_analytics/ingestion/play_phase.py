"""Event context from source play patterns; never a continuous ball-state estimate."""

from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.possession_metadata import build_possession_metadata
from football_analytics.ingestion.possession_sequences import PossessionSequenceResult
from football_analytics.integrations.base import (
    MatchEvent,
    MatchEventContext,
    MatchEventPlayMetadata,
)
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary

type PlayPhase = Literal["open_play", "set_piece", "unknown"]

OPEN_PLAY_PATTERNS = frozenset({"regular_play", "from_counter"})
SET_PIECE_PATTERNS = frozenset(
    {"from_corner", "from_free_kick", "from_throw_in", "from_goal_kick", "from_kick_off"}
)


class PlayPhaseEvent(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_event_id: str
    source_index: int
    timestamp_ms: int
    fragment_number: int
    period: int
    external_team_id: str | None
    external_player_id: str | None
    event_type: str
    play_pattern: str | None
    external_play_pattern_id: str | None
    phase: PlayPhase
    reason: Literal[
        "source_open_play", "source_restart_context", "missing_pattern", "ambiguous_pattern"
    ]


class FragmentPlayPhases(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    fragment_number: int
    composition: Literal["open_play", "set_piece", "unknown", "mixed"]
    event_ids_by_phase: dict[PlayPhase, tuple[str, ...]]


class PlayPhaseResult(PossessionSequenceResult):
    phase_policy_version: Literal["1.0.0"] = "1.0.0"
    classification_basis: Literal["source_event_play_pattern"] = "source_event_play_pattern"
    events: tuple[PlayPhaseEvent, ...]
    fragment_phases: tuple[FragmentPlayPhases, ...]


def build_play_phases(
    contexts: Sequence[MatchEventContext],
    metadata: Sequence[MatchEventPlayMetadata],
    periods: Sequence[PeriodBoundary],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
) -> PlayPhaseResult:
    validated = build_possession_metadata(
        contexts,
        metadata,
        periods,
        provider=provider,
        match_id=match_id,
        events_sha256=events_sha256,
    )
    by_id = {item.external_event_id: item for item in metadata}
    events: list[PlayPhaseEvent] = []
    fragments: list[FragmentPlayPhases] = []
    for fragment in validated.fragments:
        grouped: dict[PlayPhase, list[str]] = defaultdict(list)
        for event_id in fragment.external_event_ids:
            item = by_id[event_id]
            phase: PlayPhase
            reason: Literal[
                "source_open_play", "source_restart_context", "missing_pattern", "ambiguous_pattern"
            ]
            if item.play_pattern in OPEN_PLAY_PATTERNS:
                phase, reason = "open_play", "source_open_play"
            elif item.play_pattern in SET_PIECE_PATTERNS:
                phase, reason = "set_piece", "source_restart_context"
            elif item.play_pattern is None:
                phase, reason = "unknown", "missing_pattern"
            else:
                phase, reason = "unknown", "ambiguous_pattern"
            grouped[phase].append(event_id)
            events.append(
                PlayPhaseEvent(
                    external_event_id=event_id,
                    source_index=item.source_index,
                    timestamp_ms=item.timestamp_ms,
                    fragment_number=fragment.fragment_number,
                    period=item.period,
                    external_team_id=item.external_team_id,
                    external_player_id=item.external_player_id,
                    event_type=item.event_type,
                    play_pattern=item.play_pattern,
                    external_play_pattern_id=item.external_play_pattern_id,
                    phase=phase,
                    reason=reason,
                )
            )
        fragments.append(
            FragmentPlayPhases(
                fragment_number=fragment.fragment_number,
                composition=next(iter(grouped)) if len(grouped) == 1 else "mixed",
                event_ids_by_phase={key: tuple(ids) for key, ids in sorted(grouped.items())},
            )
        )
    return PlayPhaseResult(
        **{key: getattr(validated, key) for key in PossessionSequenceResult.model_fields},
        events=tuple(events),
        fragment_phases=tuple(fragments),
    )


def select_phase_events[T: MatchEvent](
    actions: Sequence[T],
    classification: PlayPhaseResult,
    *,
    phase: PlayPhase,
    periods: tuple[int, ...] | None = None,
) -> tuple[T, ...]:
    """Select normalized actions for existing creation/progression/xG aggregators."""
    if phase not in {"open_play", "set_piece", "unknown"}:
        raise ValueError("Unsupported play phase")
    selected_periods = classification.available_periods if periods is None else periods
    if (
        not selected_periods
        or len(set(selected_periods)) != len(selected_periods)
        or any(
            type(p) is not int or p not in classification.available_periods
            for p in selected_periods
        )
    ):
        raise ValueError("Invalid phase selection periods")
    by_id = {item.external_event_id: item for item in classification.events}
    excluded = set(classification.excluded_shootout_event_ids)
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    selected: list[T] = []
    for action in sorted(actions, key=lambda item: item.source_index):
        if (action.provider, action.external_match_id) != (
            classification.provider,
            classification.external_match_id,
        ):
            raise ValueError("Action belongs to another source or match")
        if action.external_event_id in seen_ids or action.source_index in seen_indexes:
            raise ValueError("Duplicate phase selection action")
        seen_ids.add(action.external_event_id)
        seen_indexes.add(action.source_index)
        if action.period == 5 and action.external_event_id in excluded:
            continue
        item = by_id.get(action.external_event_id)
        if item is None:
            raise ValueError("Action is outside phase source coverage")
        if any(
            getattr(action, field) != getattr(item, field)
            for field in (
                "source_index",
                "timestamp_ms",
                "period",
                "external_team_id",
                "external_player_id",
                "event_type",
            )
        ):
            raise ValueError("Action header differs from phase source")
        if item.period in selected_periods and item.phase == phase:
            selected.append(action)
    return tuple(selected)
