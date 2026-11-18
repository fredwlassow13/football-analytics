from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.integrations.base import (
    MatchEvent,
    MatchEventRelations,
    MatchGoalkeeperAction,
    MatchShot,
)

type LinkDirection = Literal["goalkeeper_to_shot", "shot_to_goalkeeper"]

type PairIssue = Literal[
    "period_mismatch",
    "same_team",
    "same_player",
    "missing_team_reference",
    "missing_player_reference",
    "multiple_shots_for_goalkeeper",
    "multiple_goalkeepers_for_shot",
]


class RelationshipIssue(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_source_event_id: str
    external_target_event_id: str
    reason: Literal[
        "missing_related_target",
        "duplicate_related_target",
        "self_reference",
    ]


class GoalkeeperShotPair(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_goalkeeper_event_id: str
    external_shot_event_id: str
    evidence: tuple[LinkDirection, ...]
    issues: tuple[PairIssue, ...]


class GoalkeeperShotResolution(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    external_match_id: str = Field(min_length=1)
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: Literal["1.0.0"] = "1.0.0"
    pairs: tuple[GoalkeeperShotPair, ...]
    source_issues: tuple[RelationshipIssue, ...]
    goalkeeper_event_ids: tuple[str, ...]
    shot_event_ids: tuple[str, ...]
    unlinked_goalkeeper_event_ids: tuple[str, ...]
    unlinked_shot_event_ids: tuple[str, ...]


def _index_details[T: MatchEvent](
    events: Sequence[T],
    *,
    expected_type: type[T],
    event_type: str,
    catalogue: dict[str, MatchEventRelations],
) -> dict[str, T]:
    indexed: dict[str, T] = {}
    header_fields = set(MatchEvent.model_fields)

    for event in events:
        if not isinstance(event, expected_type):
            raise ValueError("Unsupported detail event type")

        if event.external_event_id in indexed:
            raise ValueError("Duplicate detail source event")

        original = catalogue.get(event.external_event_id)
        if original is None:
            raise ValueError("Detail event is missing from relation catalogue")

        if original.model_dump(include=header_fields) != event.model_dump(include=header_fields):
            raise ValueError("Detail and relation headers differ")

        indexed[event.external_event_id] = event

    expected_ids = {
        event_id for event_id, relation in catalogue.items() if relation.event_type == event_type
    }
    if set(indexed) != expected_ids:
        raise ValueError("Normalized detail coverage is incomplete")

    return indexed


def resolve_goalkeeper_shot_links(
    goalkeepers: Sequence[MatchGoalkeeperAction],
    shots: Sequence[MatchShot],
    relations: Sequence[MatchEventRelations],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
) -> GoalkeeperShotResolution:
    catalogue: dict[str, MatchEventRelations] = {}
    seen_indexes: set[int] = set()

    for relation in relations:
        if not isinstance(relation, MatchEventRelations):
            raise ValueError("Unsupported relation event type")

        if (relation.provider, relation.external_match_id) != (
            provider,
            match_id,
        ):
            raise ValueError("Relations differ from requested provider and match")

        if relation.external_event_id in catalogue or relation.source_index in seen_indexes:
            raise ValueError("Duplicate relation source event")

        if relation.period not in {1, 2, 3, 4, 5}:
            raise ValueError("Unsupported relation period")

        catalogue[relation.external_event_id] = relation
        seen_indexes.add(relation.source_index)

    keepers_by_id = _index_details(
        goalkeepers,
        expected_type=MatchGoalkeeperAction,
        event_type="Goal Keeper",
        catalogue=catalogue,
    )
    shots_by_id = _index_details(
        shots,
        expected_type=MatchShot,
        event_type="Shot",
        catalogue=catalogue,
    )

    for keeper in keepers_by_id.values():
        if keeper.related_event_ids != catalogue[keeper.external_event_id].related_event_ids:
            raise ValueError("Goalkeeper and catalogue relationships differ")

    candidates: dict[tuple[str, str], set[LinkDirection]] = defaultdict(set)
    source_issues: list[RelationshipIssue] = []

    relevant_ids = set(keepers_by_id) | set(shots_by_id)
    relevant_relations = sorted(
        (catalogue[event_id] for event_id in relevant_ids),
        key=lambda item: item.source_index,
    )

    for source in relevant_relations:
        counts = Counter(source.related_event_ids or ())

        for target_id, count in counts.items():
            if count > 1:
                source_issues.append(
                    RelationshipIssue(
                        external_source_event_id=source.external_event_id,
                        external_target_event_id=target_id,
                        reason="duplicate_related_target",
                    )
                )

            if target_id == source.external_event_id:
                source_issues.append(
                    RelationshipIssue(
                        external_source_event_id=source.external_event_id,
                        external_target_event_id=target_id,
                        reason="self_reference",
                    )
                )
                continue

            target = catalogue.get(target_id)
            if target is None:
                source_issues.append(
                    RelationshipIssue(
                        external_source_event_id=source.external_event_id,
                        external_target_event_id=target_id,
                        reason="missing_related_target",
                    )
                )
                continue

            if source.event_type == "Goal Keeper" and target.event_type == "Shot":
                candidates[(source.external_event_id, target_id)].add("goalkeeper_to_shot")
            elif source.event_type == "Shot" and target.event_type == "Goal Keeper":
                candidates[(target_id, source.external_event_id)].add("shot_to_goalkeeper")

    keeper_degree = Counter(keeper_id for keeper_id, _ in candidates)
    shot_degree = Counter(shot_id for _, shot_id in candidates)
    pairs: list[GoalkeeperShotPair] = []

    ordered_candidates = sorted(
        candidates.items(),
        key=lambda item: (
            keepers_by_id[item[0][0]].source_index,
            shots_by_id[item[0][1]].source_index,
        ),
    )

    for (keeper_id, shot_id), directions in ordered_candidates:
        keeper_action = keepers_by_id[keeper_id]
        shot_action = shots_by_id[shot_id]
        issues: list[PairIssue] = []

        if keeper_action.period != shot_action.period:
            issues.append("period_mismatch")

        keeper_team = keeper_action.external_team_id
        shot_team = shot_action.external_team_id

        if keeper_team is None or shot_team is None:
            issues.append("missing_team_reference")
        elif keeper_team == shot_team:
            issues.append("same_team")

        keeper_player = keeper_action.external_player_id
        shot_player = shot_action.external_player_id

        if keeper_player is None or shot_player is None:
            issues.append("missing_player_reference")
        elif keeper_player == shot_player:
            issues.append("same_player")

        if keeper_degree[keeper_id] > 1:
            issues.append("multiple_shots_for_goalkeeper")

        if shot_degree[shot_id] > 1:
            issues.append("multiple_goalkeepers_for_shot")

        pairs.append(
            GoalkeeperShotPair(
                external_goalkeeper_event_id=keeper_id,
                external_shot_event_id=shot_id,
                evidence=tuple(sorted(directions)),
                issues=tuple(issues),
            )
        )

    ordered_keepers = sorted(keepers_by_id.values(), key=lambda item: item.source_index)
    ordered_shots = sorted(shots_by_id.values(), key=lambda item: item.source_index)

    return GoalkeeperShotResolution(
        provider=provider,
        external_match_id=match_id,
        events_sha256=events_sha256,
        pairs=tuple(pairs),
        source_issues=tuple(source_issues),
        goalkeeper_event_ids=tuple(action.external_event_id for action in ordered_keepers),
        shot_event_ids=tuple(action.external_event_id for action in ordered_shots),
        unlinked_goalkeeper_event_ids=tuple(
            action.external_event_id
            for action in ordered_keepers
            if keeper_degree[action.external_event_id] == 0
        ),
        unlinked_shot_event_ids=tuple(
            action.external_event_id
            for action in ordered_shots
            if shot_degree[action.external_event_id] == 0
        ),
    )
