from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.match_end_presence import (
    MatchEndPresence,
)
from football_analytics.ingestion.shootout_eligibility import (
    ShootoutEligibilityAssessment,
    ShootoutEligibilityContext,
    assess_initial_shootout_eligibility,
)
from football_analytics.ingestion.shootout_roster import (
    ShootoutRosterPlayer,
    build_shootout_roster,
)
from football_analytics.integrations.base import MatchEvent, MatchLineup


class ShootoutPlayerAssessment(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    roster: ShootoutRosterPlayer
    match_end_presence: MatchEndPresence
    eligibility: ShootoutEligibilityAssessment
    status: Literal["consistent", "review"]
    issues: tuple[str, ...]


def build_shootout_assessment(
    events: Sequence[MatchEvent],
    lineups: Sequence[MatchLineup],
    presence: Sequence[MatchEndPresence],
    contexts: Mapping[str, ShootoutEligibilityContext],
    *,
    provider: str,
    match_id: str,
) -> tuple[ShootoutPlayerAssessment, ...]:
    roster = build_shootout_roster(
        events,
        lineups,
        provider=provider,
        match_id=match_id,
    )

    presence_by_player = {item.external_player_id: item for item in presence}
    roster_ids = {item.lineup.external_player_id for item in roster}

    if len(presence_by_player) != len(presence):
        raise ValueError("Duplicate match-end presence player")

    if set(presence_by_player) != roster_ids:
        raise ValueError("Presence must cover exactly the listed players")

    if set(contexts) != roster_ids:
        raise ValueError("Contexts must cover exactly the listed players")

    result: list[ShootoutPlayerAssessment] = []

    for row in roster:
        player_id = row.lineup.external_player_id
        player_presence = presence_by_player[player_id]

        if (
            player_presence.external_match_id,
            player_presence.external_team_id,
        ) != (
            match_id,
            row.lineup.external_team_id,
        ):
            raise ValueError("Match-end presence identity differs from lineup")

        eligibility = assess_initial_shootout_eligibility(
            player_presence,
            contexts[player_id],
        )

        issues = list(eligibility.issues)

        if eligibility.status == "unknown":
            issues.append("eligibility_unknown")

        if row.participation_status == "observed" and eligibility.status == "ineligible":
            issues.append("observed_participant_classified_ineligible")

        result.append(
            ShootoutPlayerAssessment(
                roster=row,
                match_end_presence=player_presence,
                eligibility=eligibility,
                status="review" if issues else "consistent",
                issues=tuple(sorted(set(issues))),
            )
        )

    return tuple(result)
