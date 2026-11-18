from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.integrations.base import MatchEvent


class OwnGoalEvidence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_event_id: str
    source_index: int
    period: int
    timestamp_ms: int
    event_type: Literal["Own Goal For", "Own Goal Against"]
    external_team_id: str | None
    external_player_id: str | None
    scoring_team_id: str | None
    conceding_team_id: str | None
    status: Literal["usable", "review"]
    issues: tuple[str, ...]


def collect_own_goal_evidence(
    events: Sequence[MatchEvent],
    *,
    provider: str,
    match_id: str,
    team_ids: tuple[str, str],
) -> tuple[OwnGoalEvidence, ...]:
    """Collect source perspectives, without counting unique scoring episodes."""
    if not provider or not match_id:
        raise ValueError("Provider and match are required")

    if len(team_ids) != 2 or not all(team_ids) or len(set(team_ids)) != 2:
        raise ValueError("Two distinct teams are required")

    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    result: list[OwnGoalEvidence] = []

    for event in sorted(events, key=lambda item: item.source_index):
        if (event.provider, event.external_match_id) != (provider, match_id):
            raise ValueError("Event belongs to another source or match")

        if event.external_event_id in seen_ids or event.source_index in seen_indexes:
            raise ValueError("Duplicate source event")

        seen_ids.add(event.external_event_id)
        seen_indexes.add(event.source_index)

        if event.period not in {1, 2, 3, 4, 5}:
            raise ValueError("Unsupported event period")

        if event.period == 5:
            continue

        own_goal_type: Literal["Own Goal For", "Own Goal Against"]

        if event.event_type == "Own Goal For":
            own_goal_type = "Own Goal For"
        elif event.event_type == "Own Goal Against":
            own_goal_type = "Own Goal Against"
        else:
            continue

        scoring_team = None
        conceding_team = None
        issues: list[str] = []

        if event.external_team_id is None:
            issues.append("missing_team")
        else:
            if event.external_team_id not in team_ids:
                raise ValueError("Own goal references another team")

            opponent = next(team for team in team_ids if team != event.external_team_id)

            if event.event_type == "Own Goal For":
                scoring_team = event.external_team_id
                conceding_team = opponent
            else:
                scoring_team = opponent
                conceding_team = event.external_team_id

        result.append(
            OwnGoalEvidence(
                external_event_id=event.external_event_id,
                source_index=event.source_index,
                period=event.period,
                timestamp_ms=event.timestamp_ms,
                event_type=own_goal_type,
                external_team_id=event.external_team_id,
                external_player_id=event.external_player_id,
                scoring_team_id=scoring_team,
                conceding_team_id=conceding_team,
                status="review" if issues else "usable",
                issues=tuple(issues),
            )
        )

    return tuple(result)
