"""Evidence-bounded attacks and stoppages, without inferred possession percentages."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.control_sequence_analysis import _align
from football_analytics.ingestion.possession_metadata import build_possession_metadata
from football_analytics.ingestion.possession_sequences import PossessionSequenceResult
from football_analytics.integrations.base import (
    MatchEventContext,
    MatchEventPlayMetadata,
    MatchFoulCommitted,
    MatchFoulWon,
    MatchPass,
    MatchShot,
)
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary

type BoundaryReason = Literal[
    "restart",
    "out",
    "goal",
    "offside",
    "foul",
    "injury_stoppage",
    "possession_change",
    "source_boundary",
    "period_end",
    "source_end",
    "unknown_start",
    "unobserved_restart",
    "source_gap",
]

RESTART_PASSES = frozenset({"Corner", "Free Kick", "Throw-in", "Goal Kick", "Kick Off"})
RESTART_SHOTS = frozenset({"Corner", "Free Kick", "Penalty", "Kick Off"})
CONTROL_TYPES = frozenset(
    {
        "Pass",
        "Carry",
        "Shot",
        "Dribble",
        "Ball Receipt*",
        "Ball Recovery",
        "Interception",
        "Dispossessed",
        "Miscontrol",
        "Shield",
    }
)
START_TYPES = frozenset({"Pass", "Carry", "Shot", "Dribble", "Shield"})
ADMIN_TYPES = frozenset(
    {
        "Starting XI",
        "Tactical Shift",
        "Substitution",
        "Bad Behaviour",
        "Player On",
        "Player Off",
        "Half Start",
        "Half End",
        "Injury Stoppage",
    }
)


class AttackBoundaryEvidence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_event_id: str
    period: int
    reason: BoundaryReason
    timestamp_ms: int
    effective_timestamp_ms: Decimal | None
    advantage: bool | None = None
    certainty: Literal["explicit", "provider_absence_policy", "possible"] = "explicit"


class AttackSequence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    attack_number: int = Field(gt=0)
    period: int
    external_team_id: str | None
    fragment_numbers: tuple[int, ...]
    external_event_ids: tuple[str, ...]
    control_event_ids: tuple[str, ...]
    shot_event_ids: tuple[str, ...]
    start_reason: BoundaryReason
    end_reason: BoundaryReason
    start_evidence_event_id: str
    end_evidence_event_id: str | None
    first_timestamp_ms: int
    last_timestamp_ms: int
    observed_span_ms: int | None
    status: Literal["bounded", "review"]
    issues: tuple[str, ...]


class StoppageObservation(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    period: int
    stop_event_ids: tuple[str, ...]
    reasons: tuple[BoundaryReason, ...]
    restart_event_id: str | None
    closure: Literal["explicit_restart", "period_end", "unobserved_restart", "source_end"]
    start_timestamp_ms: Decimal | None
    end_timestamp_ms: int | None
    duration_ms: Decimal | None
    status: Literal["bounded", "review"]
    issues: tuple[str, ...]


class AttackSequenceResult(PossessionSequenceResult):
    attack_policy_version: Literal["1.0.0"] = "1.0.0"
    population: Literal["observed_control_spells"] = "observed_control_spells"
    attacks: tuple[AttackSequence, ...]
    boundary_evidence: tuple[AttackBoundaryEvidence, ...]
    stoppages: tuple[StoppageObservation, ...]
    unassigned_event_ids: tuple[str, ...]
    possession_duration_ms: None = None
    possession_percentage: None = None


def build_attack_sequences(
    contexts: Sequence[MatchEventContext],
    metadata: Sequence[MatchEventPlayMetadata],
    passes: Sequence[MatchPass],
    shots: Sequence[MatchShot],
    fouls: Sequence[MatchFoulCommitted | MatchFoulWon],
    periods: Sequence[PeriodBoundary],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
) -> AttackSequenceResult:
    source = build_possession_metadata(
        contexts,
        metadata,
        periods,
        provider=provider,
        match_id=match_id,
        events_sha256=events_sha256,
    )
    catalogue = {item.external_event_id: item for item in contexts}
    details = {item.external_event_id: item for item in metadata}
    pass_by_id = _align(
        passes, catalogue, {key for key, item in catalogue.items() if item.event_type == "Pass"}
    )
    shot_by_id = _align(
        shots, catalogue, {key for key, item in catalogue.items() if item.event_type == "Shot"}
    )
    foul_by_id = _align(
        fouls,
        catalogue,
        {
            key
            for key, item in catalogue.items()
            if item.event_type in {"Foul Committed", "Foul Won"}
        },
    )
    fragments = {
        key: fragment for fragment in source.fragments for key in fragment.external_event_ids
    }
    evidence: list[AttackBoundaryEvidence] = []
    restarts: set[str] = set()
    stops: dict[str, AttackBoundaryEvidence] = {}
    for key in source.external_event_ids:
        item, meta = catalogue[key], details[key]
        restart = (key in pass_by_id and pass_by_id[key].pass_type in RESTART_PASSES) or (
            key in shot_by_id and shot_by_id[key].shot_type in RESTART_SHOTS
        )
        if restart:
            restarts.add(key)
            evidence.append(
                AttackBoundaryEvidence(
                    external_event_id=key,
                    period=item.period,
                    reason="restart",
                    timestamp_ms=item.timestamp_ms,
                    effective_timestamp_ms=Decimal(item.timestamp_ms),
                )
            )
        reason: BoundaryReason | None = None
        effective: Decimal | None = Decimal(item.timestamp_ms)
        certainty: Literal["explicit", "provider_absence_policy", "possible"] = "explicit"
        advantage: bool | None = None
        if (key in shot_by_id and shot_by_id[key].is_goal) or item.event_type in {
            "Own Goal For",
            "Own Goal Against",
        }:
            reason = "goal"
        elif meta.out is True or (
            key in pass_by_id and pass_by_id[key].outcome in {"out", "injury_clearance"}
        ):
            reason = "out"
        elif item.event_type == "Offside" or (
            key in pass_by_id and pass_by_id[key].outcome == "offside"
        ):
            reason = "offside"
        elif key in foul_by_id:
            advantage = foul_by_id[key].advantage
            linked_advantage = any(
                other in foul_by_id and foul_by_id[other].advantage is True
                for other in (foul_by_id[key].related_event_ids or ())
            )
            if advantage is not True and not linked_advantage:
                reason = "foul"
                if advantage is None:
                    # StatsBomb records advantage only when true. Keep absence visible.
                    certainty = "provider_absence_policy" if provider == "statsbomb" else "possible"
        elif item.event_type == "Injury Stoppage":
            # in_chain describes prior ownership, not continuation of live play.
            reason = "injury_stoppage"
        if reason is not None:
            if reason in {"out", "goal", "offside"} and item.event_type in {"Pass", "Shot"}:
                effective = (
                    Decimal(item.timestamp_ms) + Decimal(str(meta.duration_seconds)) * 1000
                    if meta.duration_seconds is not None
                    else None
                )
            stop = AttackBoundaryEvidence(
                external_event_id=key,
                period=item.period,
                reason=reason,
                timestamp_ms=item.timestamp_ms,
                effective_timestamp_ms=effective,
                advantage=advantage,
                certainty=certainty,
            )
            evidence.append(stop)
            stops[key] = stop

    attacks: list[AttackSequence] = []
    stoppages: list[StoppageObservation] = []
    current: list[MatchEventContext] = []
    pending: list[AttackBoundaryEvidence] = []
    start_reason: BoundaryReason = "unknown_start"
    start_id = ""
    owner: str | None = None
    last_period: int | None = None
    previous: MatchEventContext | None = None
    next_start: BoundaryReason = "unknown_start"

    def finish_attack(reason: BoundaryReason, boundary_id: str | None) -> None:
        if not current:
            return
        first, last = current[0], current[-1]
        issues: set[str] = {
            issue for event in current for issue in fragments[event.external_event_id].issues
        }
        if boundary_id in stops and stops[boundary_id].certainty == "possible":
            issues.add("uncertain_stop")
        if any(
            event.event_type in START_TYPES and event.external_team_id != owner for event in current
        ):
            issues.add("conflicting_control_team")
        if any(
            event.event_type in START_TYPES
            and fragments[event.external_event_id].external_possession_team_id
            != event.external_team_id
            for event in current
        ):
            issues.add("source_possession_team_disagreement")
        if owner is None:
            issues.add("unknown_team")
        if start_reason in {"unknown_start", "source_boundary", "unobserved_restart", "source_gap"}:
            issues.add("uncertain_start")
        if reason in {"source_end", "source_boundary", "source_gap"}:
            issues.add("uncertain_end")
        if any(
            right.timestamp_ms < left.timestamp_ms
            for left, right in zip(current, current[1:], strict=False)
        ):
            issues.add("timestamp_regression")
        attacks.append(
            AttackSequence(
                attack_number=len(attacks) + 1,
                period=first.period,
                external_team_id=owner,
                fragment_numbers=tuple(
                    dict.fromkeys(fragments[e.external_event_id].fragment_number for e in current)
                ),
                external_event_ids=tuple(e.external_event_id for e in current),
                control_event_ids=tuple(
                    e.external_event_id for e in current if e.event_type in CONTROL_TYPES
                ),
                shot_event_ids=tuple(
                    e.external_event_id for e in current if e.event_type == "Shot"
                ),
                start_reason=start_reason,
                end_reason=reason,
                start_evidence_event_id=start_id,
                end_evidence_event_id=boundary_id,
                first_timestamp_ms=first.timestamp_ms,
                last_timestamp_ms=last.timestamp_ms,
                observed_span_ms=(
                    None
                    if issues.intersection(
                        {
                            "timestamp_regression",
                            "timestamp_after_period_end",
                            "period_order_regression",
                        }
                    )
                    else last.timestamp_ms - first.timestamp_ms
                ),
                status="review" if issues else "bounded",
                issues=tuple(sorted(issues)),
            )
        )
        current.clear()

    def finish_stoppage(
        closure: Literal["explicit_restart", "period_end", "unobserved_restart", "source_end"],
        end: MatchEventContext | None,
    ) -> None:
        if not pending:
            return
        first = pending[0]
        start = first.effective_timestamp_ms
        end_ms = end.timestamp_ms if end is not None else None
        issues: set[str] = set()
        if any(item.certainty == "possible" for item in pending):
            issues.add("possible_stop")
        if start is not None and any(
            item.effective_timestamp_ms is not None and item.effective_timestamp_ms < start
            for item in pending
        ):
            issues.add("conflicting_stop_times")
        if start is None:
            issues.add("missing_stop_time")
        if closure in {"unobserved_restart", "source_end"}:
            issues.add("missing_explicit_restart")
        affected = [
            catalogue[key]
            for key in source.external_event_ids
            if catalogue[key].period == first.period
            and catalogue[key].source_index >= catalogue[first.external_event_id].source_index
            and (end is None or catalogue[key].source_index <= end.source_index)
        ]
        if any(fragments[item.external_event_id].issues for item in affected):
            issues.add("source_quality_issue")
        if start is not None and end_ms is not None and start > end_ms:
            issues.add("negative_interval")
        if end_ms is None or (end is not None and end.period != first.period):
            issues.add("missing_period_endpoint")
        duration = (
            Decimal(end_ms) - start
            if not issues and start is not None and end_ms is not None
            else None
        )
        stoppages.append(
            StoppageObservation(
                period=first.period,
                stop_event_ids=tuple(e.external_event_id for e in pending),
                reasons=tuple(dict.fromkeys(e.reason for e in pending)),
                restart_event_id=end.external_event_id
                if end is not None and closure == "explicit_restart"
                else None,
                closure=closure,
                start_timestamp_ms=start,
                end_timestamp_ms=end_ms,
                duration_ms=duration,
                status="review" if issues else "bounded",
                issues=tuple(sorted(issues)),
            )
        )
        pending.clear()

    for key in source.external_event_ids:
        item = catalogue[key]
        if last_period is not None and item.period != last_period:
            finish_attack("source_end", previous.external_event_id if previous else None)
            finish_stoppage("source_end", None)
            next_start = "unknown_start"
        if previous is not None and item.source_index != previous.source_index + 1:
            finish_attack("source_gap", key)
            finish_stoppage("source_end", None)
            next_start = "source_gap"
        last_period = item.period
        if item.event_type == "Half End":
            finish_attack("period_end", key)
            finish_stoppage("period_end", item)
            next_start = "unknown_start"
            previous = item
            continue
        fragment = fragments[key]
        initiates = item.event_type in START_TYPES and item.external_team_id is not None
        if key in restarts:
            finish_attack("restart", key)
            finish_stoppage("explicit_restart", item)
            next_start = "restart"
        elif pending and item.event_type in CONTROL_TYPES:
            finish_stoppage("unobserved_restart", item)
            next_start = "unobserved_restart"
        if current and initiates:
            old_fragment = fragments[current[-1].external_event_id]
            if owner != item.external_team_id:
                finish_attack("possession_change", key)
                next_start = "possession_change"
            elif old_fragment.fragment_number != fragment.fragment_number:
                finish_attack("source_boundary", key)
                next_start = "source_boundary"
        if not current and (initiates or key in restarts):
            start_reason, start_id = next_start, key
            owner = item.external_team_id
            current.append(item)
        elif current and item.event_type not in ADMIN_TYPES:
            current.append(item)
        if key in stops:
            finish_attack(stops[key].reason, key)
            pending.append(stops[key])
            next_start = "unobserved_restart"
        previous = item
    finish_attack("source_end", None)
    finish_stoppage("source_end", None)
    assigned = {key for attack in attacks for key in attack.external_event_ids}
    return AttackSequenceResult(
        **{key: getattr(source, key) for key in PossessionSequenceResult.model_fields},
        attacks=tuple(attacks),
        boundary_evidence=tuple(evidence),
        stoppages=tuple(stoppages),
        unassigned_event_ids=tuple(key for key in source.external_event_ids if key not in assigned),
    )
