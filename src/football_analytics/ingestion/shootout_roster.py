from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.shootout_participation import (
    ShootoutParticipationEvidence,
    collect_shootout_participation,
)
from football_analytics.integrations.base import MatchEvent, MatchLineup


class ShootoutRosterPlayer(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    lineup: MatchLineup
    evidence: tuple[ShootoutParticipationEvidence, ...]
    participation_status: Literal["observed", "unknown"]


def build_shootout_roster(
    events: Sequence[MatchEvent],
    lineups: Sequence[MatchLineup],
    *,
    provider: str,
    match_id: str,
) -> tuple[ShootoutRosterPlayer, ...]:
    evidence = collect_shootout_participation(
        events,
        provider=provider,
        match_id=match_id,
    )

    roster: dict[str, MatchLineup] = {}

    for lineup in lineups:
        if (lineup.provider, lineup.external_match_id) != (provider, match_id):
            raise ValueError("Lineup belongs to another source or match")

        player_id = lineup.external_player_id
        if player_id in roster:
            raise ValueError("Duplicate lineup player")

        roster[player_id] = lineup

    evidence_by_player: dict[str, list[ShootoutParticipationEvidence]] = {
        player_id: [] for player_id in roster
    }

    for item in evidence:
        if item.status == "review":
            raise ValueError(f"Shootout evidence requires review: {item.external_event_id}")

        if item.external_player_id is None:
            raise ValueError("Shootout evidence references an unlisted player")

        player_id = item.external_player_id
        if player_id not in roster:
            raise ValueError("Shootout evidence references an unlisted player")

        if item.external_team_id != roster[player_id].external_team_id:
            raise ValueError("Shootout player belongs to another team")

        evidence_by_player[player_id].append(item)

    result: list[ShootoutRosterPlayer] = []

    for player_id, lineup in sorted(
        roster.items(),
        key=lambda pair: (pair[1].external_team_id, pair[0]),
    ):
        player_evidence = tuple(evidence_by_player[player_id])

        result.append(
            ShootoutRosterPlayer(
                lineup=lineup,
                evidence=player_evidence,
                participation_status=("observed" if player_evidence else "unknown"),
            )
        )
    return tuple(result)
