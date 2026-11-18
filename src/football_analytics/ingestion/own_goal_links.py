from collections import Counter
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.own_goal_evidence import (
    collect_own_goal_evidence,
)
from football_analytics.integrations.base import (
    MatchEvent,
    MatchEventRelations,
)


class OwnGoalPair(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    against_event_id: str
    for_event_id: str
    evidence: tuple[str, ...]
    status: Literal["usable", "review"]
    issues: tuple[str, ...]


class OwnGoalLinkResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    policy_version: Literal["1.0.0"] = "1.0.0"
    pairs: tuple[OwnGoalPair, ...]
    unpaired_event_ids: tuple[str, ...]
    missing_reference_pairs: tuple[tuple[str, str], ...]


def resolve_own_goal_links(
    events: Sequence[MatchEvent],
    relations: Sequence[MatchEventRelations],
    *,
    provider: str,
    match_id: str,
    team_ids: tuple[str, str],
) -> OwnGoalLinkResult:
    own_goals = collect_own_goal_evidence(
        events,
        provider=provider,
        match_id=match_id,
        team_ids=team_ids,
    )

    catalogue = {item.external_event_id: item for item in events}
    own_by_id = {item.external_event_id: item for item in own_goals}
    relation_by_id: dict[str, MatchEventRelations] = {}
    indexes: set[int] = set()

    for relation in relations:
        key = relation.external_event_id

        if key in relation_by_id or relation.source_index in indexes:
            raise ValueError("Duplicate relation source")

        relation_by_id[key] = relation
        indexes.add(relation.source_index)

    if set(relation_by_id) != set(catalogue):
        raise ValueError("Event and relation coverage differs")

    for key, relation in relation_by_id.items():
        if any(
            getattr(relation, field) != getattr(catalogue[key], field)
            for field in MatchEvent.model_fields
        ):
            raise ValueError("Event and relation headers differ")

    edges: dict[tuple[str, str], set[str]] = {}
    missing: set[tuple[str, str]] = set()
    reference_problem_ids: set[str] = set()

    for source in own_goals:
        source_id = source.external_event_id
        targets = relation_by_id[source_id].related_event_ids or ()
        seen_targets: set[str] = set()

        for target_id in targets:
            if target_id in seen_targets or target_id == source_id:
                reference_problem_ids.add(source_id)
            seen_targets.add(target_id)

            if target_id not in catalogue:
                missing.add((source_id, target_id))
                reference_problem_ids.add(source_id)
                continue

            target = own_by_id.get(target_id)
            if target is None:
                continue

            if source.event_type == target.event_type:
                reference_problem_ids.update((source_id, target_id))
                continue

            if source.event_type == "Own Goal Against":
                pair_key = (source_id, target_id)
                direction = "against_to_for"
            else:
                pair_key = (target_id, source_id)
                direction = "for_to_against"

            edges.setdefault(pair_key, set()).add(direction)

    degree: Counter[str] = Counter()
    for against_id, for_id in edges:
        degree.update((against_id, for_id))

    pairs: list[OwnGoalPair] = []

    for against_id, for_id in sorted(
        edges,
        key=lambda pair: (
            own_by_id[pair[0]].source_index,
            own_by_id[pair[1]].source_index,
        ),
    ):
        against = own_by_id[against_id]
        own_for = own_by_id[for_id]
        issues: list[str] = []

        if degree[against_id] > 1 or degree[for_id] > 1:
            issues.append("ambiguous_counterpart")

        if against.period != own_for.period:
            issues.append("different_periods")

        if against.timestamp_ms != own_for.timestamp_ms:
            issues.append("different_timestamps")

        if against.status == "review" or own_for.status == "review":
            issues.append("incomplete_team_evidence")

        if (
            against.scoring_team_id != own_for.scoring_team_id
            or against.conceding_team_id != own_for.conceding_team_id
        ):
            issues.append("conflicting_teams")

        if {against_id, for_id}.intersection(reference_problem_ids):
            issues.append("source_reference_issue")

        pairs.append(
            OwnGoalPair(
                against_event_id=against_id,
                for_event_id=for_id,
                evidence=tuple(sorted(edges[(against_id, for_id)])),
                status="review" if issues else "usable",
                issues=tuple(sorted(issues)),
            )
        )

    return OwnGoalLinkResult(
        pairs=tuple(pairs),
        unpaired_event_ids=tuple(
            item.external_event_id for item in own_goals if degree[item.external_event_id] == 0
        ),
        missing_reference_pairs=tuple(sorted(missing)),
    )
