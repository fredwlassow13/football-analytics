from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.ball_control_statistics import (
    CONTROL_EVENT_TYPES,
    ControlFlagCoverage,
)
from football_analytics.ingestion.possession_sequences import (
    PossessionSequenceResult,
    build_possession_sequences,
)
from football_analytics.integrations.base import (
    MatchEvent,
    MatchEventContext,
    MatchEventPressureMetadata,
    MatchEventRelations,
    MatchShot,
)
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary

RelationDirection = Literal["first_to_second", "second_to_first"]
RelationLocation = Literal[
    "same_fragment",
    "different_fragments",
    "different_periods",
    "outside_playing_fragments",
]
NextFragmentRelation = Literal[
    "same_team",
    "different_team",
    "unknown_team",
    "no_next_fragment_in_period",
]


class FragmentFlagEvidence(ControlFlagCoverage):
    true_event_ids: tuple[str, ...]
    false_event_ids: tuple[str, ...]
    unknown_event_ids: tuple[str, ...]


class ControlFragmentObservation(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    fragment_number: int
    period: int
    control_event_ids: tuple[str, ...]
    under_pressure: FragmentFlagEvidence
    counterpress: FragmentFlagEvidence
    shot_event_ids: tuple[str, ...]
    shot_goal_event_ids: tuple[str, ...]
    shot_outcome_event_ids: dict[str, tuple[str, ...]]
    possession_team_shot_ids: tuple[str, ...]
    other_team_shot_ids: tuple[str, ...]
    unknown_team_shot_ids: tuple[str, ...]
    own_goal_for_event_ids: tuple[str, ...]
    own_goal_against_event_ids: tuple[str, ...]
    last_event_id: str
    last_event_type: str
    next_fragment_number: int | None
    next_fragment_relation: NextFragmentRelation


class ControlRelationPair(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    first_event_id: str
    second_event_id: str
    first_fragment_number: int | None
    second_fragment_number: int | None
    evidence: tuple[RelationDirection, ...]
    location: RelationLocation


class ControlReferenceIssue(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    source_event_id: str
    target_event_id: str
    code: Literal["target_missing", "self_reference", "duplicate_reference"]


class ControlSequenceAnalysis(PossessionSequenceResult):
    analysis_policy_version: Literal["1.0.0"] = "1.0.0"
    control_population_basis: Literal["observed_control_actions"] = "observed_control_actions"
    control_event_ids: tuple[str, ...]
    observations: tuple[ControlFragmentObservation, ...]
    relation_pairs: tuple[ControlRelationPair, ...]
    reference_issues: tuple[ControlReferenceIssue, ...]


def _align[T: MatchEvent](
    events: Sequence[T],
    catalogue: dict[str, MatchEventContext],
    expected_ids: set[str],
) -> dict[str, T]:
    by_id: dict[str, T] = {}
    indexes: set[int] = set()

    for event in events:
        if event.external_event_id in by_id or event.source_index in indexes:
            raise ValueError("Duplicate aligned source event")
        by_id[event.external_event_id] = event
        indexes.add(event.source_index)

    if set(by_id) != expected_ids:
        raise ValueError("Aligned source coverage mismatch")

    for event_id, event in by_id.items():
        context = catalogue[event_id]
        for field in MatchEvent.model_fields:
            if getattr(event, field) != getattr(context, field):
                raise ValueError(f"Aligned header mismatch: {field}")

    return by_id


def _flags(
    event_ids: Sequence[str],
    metadata: dict[str, MatchEventPressureMetadata],
    field: Literal["under_pressure", "counterpress"],
) -> FragmentFlagEvidence:
    true_ids = tuple(
        event_id for event_id in event_ids if getattr(metadata[event_id], field) is True
    )
    false_ids = tuple(
        event_id for event_id in event_ids if getattr(metadata[event_id], field) is False
    )
    unknown_ids = tuple(
        event_id for event_id in event_ids if getattr(metadata[event_id], field) is None
    )
    return FragmentFlagEvidence(
        true=len(true_ids),
        false=len(false_ids),
        unknown=len(unknown_ids),
        true_event_ids=true_ids,
        false_event_ids=false_ids,
        unknown_event_ids=unknown_ids,
    )


def build_control_sequence_analysis(
    contexts: Sequence[MatchEventContext],
    periods: Sequence[PeriodBoundary],
    relations: Sequence[MatchEventRelations],
    pressure_metadata: Sequence[MatchEventPressureMetadata],
    shots: Sequence[MatchShot],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
) -> ControlSequenceAnalysis:
    sequences = build_possession_sequences(
        contexts,
        periods,
        provider=provider,
        match_id=match_id,
        events_sha256=events_sha256,
    )
    catalogue = {event.external_event_id: event for event in contexts}
    all_ids = set(catalogue)

    relations_by_id = _align(relations, catalogue, all_ids)
    metadata_by_id = _align(pressure_metadata, catalogue, all_ids)
    shots_by_id = _align(
        shots,
        catalogue,
        {event_id for event_id, event in catalogue.items() if event.event_type == "Shot"},
    )

    for event_id, metadata in metadata_by_id.items():
        if metadata.under_pressure is not catalogue[event_id].under_pressure:
            raise ValueError("Pressure metadata differs from event context")

    fragment_by_id = {
        event_id: fragment
        for fragment in sequences.fragments
        for event_id in fragment.external_event_ids
    }
    control_ids = tuple(
        event_id
        for event_id in sequences.external_event_ids
        if catalogue[event_id].event_type in CONTROL_EVENT_TYPES
    )
    control_set = set(control_ids)
    next_by_number = {
        left.fragment_number: right
        for left, right in zip(sequences.fragments, sequences.fragments[1:], strict=False)
        if left.period == right.period
    }
    observations: list[ControlFragmentObservation] = []

    for fragment in sequences.fragments:
        fragment_controls = tuple(
            event_id for event_id in fragment.external_event_ids if event_id in control_set
        )
        fragment_shots = [
            shots_by_id[event_id]
            for event_id in fragment.external_event_ids
            if event_id in shots_by_id
        ]
        owner = fragment.external_possession_team_id
        owner_shots: list[str] = []
        other_shots: list[str] = []
        unknown_shots: list[str] = []
        outcomes: dict[str, list[str]] = defaultdict(list)

        for shot in fragment_shots:
            event_id = shot.external_event_id
            outcomes[shot.outcome].append(event_id)
            if owner is None or shot.external_team_id is None:
                unknown_shots.append(event_id)
            elif shot.external_team_id == owner:
                owner_shots.append(event_id)
            else:
                other_shots.append(event_id)

        next_fragment = next_by_number.get(fragment.fragment_number)
        next_relation: NextFragmentRelation
        if next_fragment is None:
            next_relation = "no_next_fragment_in_period"
        elif owner is None or next_fragment.external_possession_team_id is None:
            next_relation = "unknown_team"
        elif owner == next_fragment.external_possession_team_id:
            next_relation = "same_team"
        else:
            next_relation = "different_team"

        last_id = fragment.external_event_ids[-1]
        observations.append(
            ControlFragmentObservation(
                fragment_number=fragment.fragment_number,
                period=fragment.period,
                control_event_ids=fragment_controls,
                under_pressure=_flags(fragment_controls, metadata_by_id, "under_pressure"),
                counterpress=_flags(fragment_controls, metadata_by_id, "counterpress"),
                shot_event_ids=tuple(shot.external_event_id for shot in fragment_shots),
                shot_goal_event_ids=tuple(
                    shot.external_event_id for shot in fragment_shots if shot.is_goal
                ),
                shot_outcome_event_ids={key: tuple(ids) for key, ids in sorted(outcomes.items())},
                possession_team_shot_ids=tuple(owner_shots),
                other_team_shot_ids=tuple(other_shots),
                unknown_team_shot_ids=tuple(unknown_shots),
                own_goal_for_event_ids=tuple(
                    event_id
                    for event_id in fragment.external_event_ids
                    if catalogue[event_id].event_type == "Own Goal For"
                ),
                own_goal_against_event_ids=tuple(
                    event_id
                    for event_id in fragment.external_event_ids
                    if catalogue[event_id].event_type == "Own Goal Against"
                ),
                last_event_id=last_id,
                last_event_type=catalogue[last_id].event_type,
                next_fragment_number=(
                    None if next_fragment is None else next_fragment.fragment_number
                ),
                next_fragment_relation=next_relation,
            )
        )

    edges: dict[tuple[str, str], set[RelationDirection]] = defaultdict(set)
    reference_issues: list[ControlReferenceIssue] = []

    for relation in sorted(
        relations_by_id.values(),
        key=lambda item: item.source_index,
    ):
        source_id = relation.external_event_id
        seen_targets: set[str] = set()
        for target_id in relation.related_event_ids or ():
            if source_id not in control_set and target_id not in control_set:
                continue

            code: Literal["target_missing", "self_reference", "duplicate_reference"] | None = None
            if target_id not in catalogue:
                code = "target_missing"
            elif target_id == source_id:
                code = "self_reference"
            elif target_id in seen_targets:
                code = "duplicate_reference"
            seen_targets.add(target_id)

            if code is not None:
                reference_issues.append(
                    ControlReferenceIssue(
                        source_event_id=source_id,
                        target_event_id=target_id,
                        code=code,
                    )
                )
                continue

            source_context = catalogue[source_id]
            target_context = catalogue[target_id]
            if source_context.source_index < target_context.source_index:
                edges[(source_id, target_id)].add("first_to_second")
            else:
                edges[(target_id, source_id)].add("second_to_first")

    pairs: list[ControlRelationPair] = []
    for first_id, second_id in sorted(
        edges,
        key=lambda pair: (
            catalogue[pair[0]].source_index,
            catalogue[pair[1]].source_index,
        ),
    ):
        first_fragment = fragment_by_id.get(first_id)
        second_fragment = fragment_by_id.get(second_id)
        location: RelationLocation
        if catalogue[first_id].period != catalogue[second_id].period:
            location = "different_periods"
        elif first_fragment is None or second_fragment is None:
            location = "outside_playing_fragments"
        elif first_fragment.fragment_number == second_fragment.fragment_number:
            location = "same_fragment"
        else:
            location = "different_fragments"

        pairs.append(
            ControlRelationPair(
                first_event_id=first_id,
                second_event_id=second_id,
                first_fragment_number=(
                    None if first_fragment is None else first_fragment.fragment_number
                ),
                second_fragment_number=(
                    None if second_fragment is None else second_fragment.fragment_number
                ),
                evidence=tuple(sorted(edges[(first_id, second_id)])),
                location=location,
            )
        )

    return ControlSequenceAnalysis(
        **sequences.model_dump(),
        control_event_ids=control_ids,
        observations=tuple(observations),
        relation_pairs=tuple(pairs),
        reference_issues=tuple(reference_issues),
    )
