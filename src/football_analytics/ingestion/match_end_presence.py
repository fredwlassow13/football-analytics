from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.match_participation import (
    PlayerParticipation,
)
from football_analytics.ingestion.participation import (
    ParticipationTransition,
    build_player_intervals,
)


class MatchEndPresence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_match_id: str
    external_team_id: str
    external_player_id: str
    status: Literal["active", "inactive", "unknown"]
    last_transition_index: int | None


def classify_match_end_presence(
    player: PlayerParticipation,
    transitions: Sequence[ParticipationTransition],
    *,
    match_duration_ms: int,
) -> MatchEndPresence:
    """Classify reconstructed activity, without deciding shootout eligibility."""
    ordered = sorted(transitions, key=lambda item: item.event_index)

    reconstructed = build_player_intervals(
        list(ordered),
        match_duration_ms,
    )
    recorded = sorted(player.intervals, key=lambda item: item.start_ms)

    if reconstructed != recorded:
        raise ValueError("Transitions differ from participation intervals")

    expected_duration = (
        sum(interval.duration_ms for interval in reconstructed) if reconstructed else None
    )
    if player.elapsed_on_field_ms != expected_duration:
        raise ValueError("Participation duration differs from intervals")

    status: Literal["active", "inactive", "unknown"]
    if not ordered:
        status = "unknown"
    elif ordered[-1].action == "enter":
        status = "active"
    else:
        status = "inactive"

    return MatchEndPresence(
        external_match_id=player.external_match_id,
        external_team_id=player.external_team_id,
        external_player_id=player.external_player_id,
        status=status,
        last_transition_index=(ordered[-1].event_index if ordered else None),
    )
