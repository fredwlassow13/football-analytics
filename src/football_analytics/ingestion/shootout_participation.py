from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.integrations.base import MatchEvent


class ShootoutParticipationEvidence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_event_id: str
    source_index: int
    external_team_id: str | None
    external_player_id: str | None
    role: Literal["shooter", "goalkeeper"]
    status: Literal["observed", "review"]
    issues: tuple[str, ...]


def collect_shootout_participation(
    events: Sequence[MatchEvent],
    *,
    provider: str,
    match_id: str,
) -> tuple[ShootoutParticipationEvidence, ...]:
    """Extract recorded involvement; absence does not establish ineligibility."""
    if not provider or not match_id:
        raise ValueError("Provider and match are required")

    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    result: list[ShootoutParticipationEvidence] = []

    for event in sorted(events, key=lambda item: item.source_index):
        if (event.provider, event.external_match_id) != (provider, match_id):
            raise ValueError("Event belongs to another source or match")

        if event.external_event_id in seen_ids or event.source_index in seen_indexes:
            raise ValueError("Duplicate source event")

        seen_ids.add(event.external_event_id)
        seen_indexes.add(event.source_index)

        if event.period not in {1, 2, 3, 4, 5}:
            raise ValueError("Unsupported event period")

        if event.period != 5:
            continue

        role: Literal["shooter", "goalkeeper"]
        if event.event_type == "Shot":
            role = "shooter"
        elif event.event_type == "Goal Keeper":
            role = "goalkeeper"
        else:
            continue

        issues: list[str] = []
        if event.external_team_id is None:
            issues.append("missing_team")
        if event.external_player_id is None:
            issues.append("missing_player")

        result.append(
            ShootoutParticipationEvidence(
                external_event_id=event.external_event_id,
                source_index=event.source_index,
                external_team_id=event.external_team_id,
                external_player_id=event.external_player_id,
                role=role,
                status="review" if issues else "observed",
                issues=tuple(issues),
            )
        )
    return tuple(result)
