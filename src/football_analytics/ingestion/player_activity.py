from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.integrations.base import MatchEventSpatial

ACTIVITY_EVENT_TYPES = frozenset(
    {
        "50/50",
        "Ball Receipt*",
        "Ball Recovery",
        "Block",
        "Carry",
        "Clearance",
        "Dispossessed",
        "Dribble",
        "Dribbled Past",
        "Duel",
        "Foul Committed",
        "Foul Won",
        "Goal Keeper",
        "Interception",
        "Miscontrol",
        "Offside",
        "Pass",
        "Pressure",
        "Shield",
        "Shot",
    }
)

NON_ACTIVITY_EVENT_TYPES = frozenset(
    {
        "Bad Behaviour",
        "Half End",
        "Half Start",
        "Injury Stoppage",
        "Player Off",
        "Player On",
        "Starting XI",
        "Substitution",
        "Tactical Shift",
    }
)


class ActivityLocationIssue(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_event_id: str
    source_index: int
    event_type: str
    reason: Literal["missing_location", "outside_pitch"]


class PlayerActivitySelection(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    external_team_id: str
    external_player_id: str
    policy_version: Literal["1.0.0"] = "1.0.0"
    periods: tuple[int, ...]
    events: tuple[MatchEventSpatial, ...]
    location_issues: tuple[ActivityLocationIssue, ...]
    excluded_event_ids: tuple[str, ...]

    @property
    def selected_count(self) -> int:
        return len(self.events)

    @property
    def candidate_count(self) -> int:
        return self.selected_count + len(self.location_issues)


def select_player_activity(
    events: Sequence[MatchEventSpatial],
    *,
    provider: str,
    match_id: str,
    team_id: str,
    player_id: str,
    periods: tuple[int, ...] = (1, 2, 3, 4),
) -> PlayerActivitySelection:
    if not all((provider, match_id, team_id, player_id)):
        raise ValueError("Provider, match, team and player are required")

    if (
        not periods
        or len(periods) != len(set(periods))
        or any(
            isinstance(period, bool) or not isinstance(period, int) or period not in {1, 2, 3, 4}
            for period in periods
        )
    ):
        raise ValueError("Periods must be unique playing periods")

    selected: list[MatchEventSpatial] = []
    issues: list[ActivityLocationIssue] = []
    excluded: list[str] = []
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()

    for event in sorted(events, key=lambda item: item.source_index):
        if (event.provider, event.external_match_id) != (provider, match_id):
            raise ValueError("Events must belong to the requested provider and match")

        if event.external_event_id in seen_ids or event.source_index in seen_indexes:
            raise ValueError("Duplicate source event")

        seen_ids.add(event.external_event_id)
        seen_indexes.add(event.source_index)

        if event.period not in periods or event.external_player_id != player_id:
            continue

        if event.external_team_id != team_id:
            raise ValueError("Player event belongs to another team")

        if event.event_type in NON_ACTIVITY_EVENT_TYPES:
            excluded.append(event.external_event_id)
            continue

        if event.event_type not in ACTIVITY_EVENT_TYPES:
            raise ValueError(f"Unclassified activity event type: {event.event_type}")

        location = event.start_location
        reason: Literal["missing_location", "outside_pitch"] | None = None

        if location is None:
            reason = "missing_location"
        elif not 0 <= location.x <= 120 or not 0 <= location.y <= 80:
            reason = "outside_pitch"

        if reason is not None:
            issues.append(
                ActivityLocationIssue(
                    external_event_id=event.external_event_id,
                    source_index=event.source_index,
                    event_type=event.event_type,
                    reason=reason,
                )
            )
        else:
            selected.append(event)

    return PlayerActivitySelection(
        provider=provider,
        external_match_id=match_id,
        external_team_id=team_id,
        external_player_id=player_id,
        periods=tuple(sorted(periods)),
        events=tuple(selected),
        location_issues=tuple(issues),
        excluded_event_ids=tuple(excluded),
    )
