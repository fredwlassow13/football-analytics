from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.integrations.base import (
    MatchEvent,
    MatchPassCreation,
    MatchShotCreation,
)

type CreationAction = MatchPassCreation | MatchShotCreation
type CreationEvidence = Literal["pass.assisted_shot_id", "shot.key_pass_id"]
type CreationReferenceField = Literal["assisted_shot_id", "key_pass_id"]
type CreationReferenceReason = Literal["missing_target", "wrong_target_type", "self_reference"]
type CreationPairIssue = Literal[
    "period_mismatch",
    "different_teams",
    "same_player",
    "missing_team_reference",
    "missing_player_reference",
    "pass_not_before_shot",
    "timestamp_order_mismatch",
    "multiple_shots_for_pass",
    "multiple_passes_for_shot",
]


class CreationReferenceIssue(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_source_event_id: str
    external_target_event_id: str
    reference_field: CreationReferenceField
    observed_target_type: str | None
    reason: CreationReferenceReason


class CreationPair(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_pass_event_id: str
    external_shot_event_id: str
    evidence: tuple[CreationEvidence, ...]
    issues: tuple[CreationPairIssue, ...]


class CreationLinkResolution(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    external_match_id: str = Field(min_length=1)
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: Literal["1.0.0"] = "1.0.0"
    pairs: tuple[CreationPair, ...]
    reference_issues: tuple[CreationReferenceIssue, ...]
    pass_event_ids: tuple[str, ...]
    shot_event_ids: tuple[str, ...]
    unlinked_pass_event_ids: tuple[str, ...]
    unlinked_shot_event_ids: tuple[str, ...]
    flagged_unlinked_pass_event_ids: tuple[str, ...]


def resolve_creation_links(
    actions: Sequence[CreationAction],
    catalogue: Sequence[MatchEvent],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
) -> CreationLinkResolution:
    catalogue_by_id: dict[str, MatchEvent] = {}
    seen_indexes: set[int] = set()

    for entry in catalogue:
        if not isinstance(entry, MatchEvent):
            raise ValueError("Unsupported catalogue event type")

        if (entry.provider, entry.external_match_id) != (provider, match_id):
            raise ValueError("Catalogue differs from requested provider and match")

        if entry.external_event_id in catalogue_by_id or entry.source_index in seen_indexes:
            raise ValueError("Duplicate catalogue source event")

        if entry.period not in {1, 2, 3, 4, 5}:
            raise ValueError("Unsupported catalogue period")

        catalogue_by_id[entry.external_event_id] = entry
        seen_indexes.add(entry.source_index)

    actions_by_id: dict[str, CreationAction] = {}
    header_fields = set(MatchEvent.model_fields)

    for action in actions:
        if not isinstance(action, (MatchPassCreation, MatchShotCreation)):
            raise ValueError("Creation actions require creation contracts")

        if action.external_event_id in actions_by_id:
            raise ValueError("Duplicate creation source event")

        original = catalogue_by_id.get(action.external_event_id)
        if original is None:
            raise ValueError("Creation action is missing from catalogue")

        if original.model_dump(include=header_fields) != action.model_dump(include=header_fields):
            raise ValueError("Creation and catalogue headers differ")

        actions_by_id[action.external_event_id] = action

    expected_ids = {
        event_id
        for event_id, entry in catalogue_by_id.items()
        if entry.event_type in {"Pass", "Shot"}
    }
    if set(actions_by_id) != expected_ids:
        raise ValueError("Creation detail coverage is incomplete")

    ordered_actions = sorted(actions_by_id.values(), key=lambda item: item.source_index)
    passes_by_id = {
        action.external_event_id: action
        for action in ordered_actions
        if isinstance(action, MatchPassCreation)
    }
    shots_by_id = {
        action.external_event_id: action
        for action in ordered_actions
        if isinstance(action, MatchShotCreation)
    }

    candidates: dict[tuple[str, str], set[CreationEvidence]] = defaultdict(set)
    reference_issues: list[CreationReferenceIssue] = []

    for action in ordered_actions:
        reference_field: CreationReferenceField
        evidence_kind: CreationEvidence

        if isinstance(action, MatchPassCreation):
            target_id = action.external_assisted_shot_event_id
            expected_type = "Shot"
            reference_field = "assisted_shot_id"
            evidence_kind = "pass.assisted_shot_id"
        else:
            target_id = action.external_key_pass_event_id
            expected_type = "Pass"
            reference_field = "key_pass_id"
            evidence_kind = "shot.key_pass_id"

        if target_id is None:
            continue

        target = catalogue_by_id.get(target_id)
        reason: CreationReferenceReason | None = None

        if target_id == action.external_event_id:
            reason = "self_reference"
        elif target is None:
            reason = "missing_target"
        elif target.event_type != expected_type:
            reason = "wrong_target_type"

        if reason is not None:
            reference_issues.append(
                CreationReferenceIssue(
                    external_source_event_id=action.external_event_id,
                    external_target_event_id=target_id,
                    reference_field=reference_field,
                    observed_target_type=(target.event_type if target is not None else None),
                    reason=reason,
                )
            )
            continue

        pair_key = (
            (action.external_event_id, target_id)
            if expected_type == "Shot"
            else (target_id, action.external_event_id)
        )
        candidates[pair_key].add(evidence_kind)

    pass_degree = Counter(pass_id for pass_id, _ in candidates)
    shot_degree = Counter(shot_id for _, shot_id in candidates)
    pairs: list[CreationPair] = []

    for (pass_id, shot_id), evidence in sorted(
        candidates.items(),
        key=lambda item: (
            passes_by_id[item[0][0]].source_index,
            shots_by_id[item[0][1]].source_index,
        ),
    ):
        pass_action = passes_by_id[pass_id]
        shot_action = shots_by_id[shot_id]
        issues: list[CreationPairIssue] = []

        if pass_action.period != shot_action.period:
            issues.append("period_mismatch")

        pass_team = pass_action.external_team_id
        shot_team = shot_action.external_team_id
        if pass_team is None or shot_team is None:
            issues.append("missing_team_reference")
        elif pass_team != shot_team:
            issues.append("different_teams")

        passer = pass_action.external_player_id
        shooter = shot_action.external_player_id
        if passer is None or shooter is None:
            issues.append("missing_player_reference")
        elif passer == shooter:
            issues.append("same_player")

        if pass_action.source_index >= shot_action.source_index:
            issues.append("pass_not_before_shot")

        if (
            pass_action.period == shot_action.period
            and pass_action.timestamp_ms > shot_action.timestamp_ms
        ):
            issues.append("timestamp_order_mismatch")

        if pass_degree[pass_id] > 1:
            issues.append("multiple_shots_for_pass")
        if shot_degree[shot_id] > 1:
            issues.append("multiple_passes_for_shot")

        pairs.append(
            CreationPair(
                external_pass_event_id=pass_id,
                external_shot_event_id=shot_id,
                evidence=tuple(sorted(evidence)),
                issues=tuple(issues),
            )
        )

    return CreationLinkResolution(
        provider=provider,
        external_match_id=match_id,
        events_sha256=events_sha256,
        pairs=tuple(pairs),
        reference_issues=tuple(reference_issues),
        pass_event_ids=tuple(passes_by_id),
        shot_event_ids=tuple(shots_by_id),
        unlinked_pass_event_ids=tuple(
            event_id for event_id in passes_by_id if pass_degree[event_id] == 0
        ),
        unlinked_shot_event_ids=tuple(
            event_id for event_id in shots_by_id if shot_degree[event_id] == 0
        ),
        flagged_unlinked_pass_event_ids=tuple(
            event_id
            for event_id, pass_action in passes_by_id.items()
            if pass_degree[event_id] == 0
            and (pass_action.shot_assist is True or pass_action.goal_assist is True)
        ),
    )
