from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.offensive_summary import (
    OffensiveSummary,
    PlayerOffensiveSummary,
)
from football_analytics.ingestion.player_scope_participation import (
    PlayerScopeParticipation,
)
from football_analytics.integrations.base import MatchLineup


class OffensiveCounts(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    passes_attempted: int = Field(default=0, ge=0)
    passes_completed: int = Field(default=0, ge=0)
    shots: int = Field(default=0, ge=0)
    shots_on_target: int = Field(default=0, ge=0)
    goals: int = Field(default=0, ge=0)
    dribbles_attempted: int = Field(default=0, ge=0)
    dribbles_completed: int = Field(default=0, ge=0)


class OffensiveRosterPlayer(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    lineup: MatchLineup
    participation: PlayerScopeParticipation
    observed_summary: PlayerOffensiveSummary | None
    counts: OffensiveCounts | None
    counts_basis: Literal[
        "observed_events",
        "zero_with_presence",
        "not_applicable",
        "unknown",
    ]


class OffensiveRosterSummary(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    offensive_summary: OffensiveSummary
    lineups_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    population_basis: Literal["listed_players"] = "listed_players"
    players: tuple[OffensiveRosterPlayer, ...]


def build_offensive_roster(
    summary: OffensiveSummary,
    lineups: Sequence[MatchLineup],
    participation: Sequence[PlayerScopeParticipation],
    *,
    lineups_sha256: str,
) -> OffensiveRosterSummary:
    roster = {item.external_player_id: item for item in lineups}
    presence = {item.external_player_id: item for item in participation}
    observed = {item.external_player_id: item for item in summary.players}

    if len(roster) != len(lineups):
        raise ValueError("Duplicate lineup player")
    if len(presence) != len(participation):
        raise ValueError("Duplicate participation player")
    if len(observed) != len(summary.players):
        raise ValueError("Duplicate offensive player")
    if set(roster) != set(presence):
        raise ValueError("Participation must cover exactly the listed players")
    if not set(observed).issubset(roster):
        raise ValueError("Offensive summary contains an unlisted player")

    result: list[OffensiveRosterPlayer] = []

    for player_id, lineup in sorted(
        roster.items(),
        key=lambda item: (item[1].external_team_id, item[0]),
    ):
        player_presence = presence[player_id]
        player_summary = observed.get(player_id)

        if (lineup.provider, lineup.external_match_id) != (
            summary.provider,
            summary.external_match_id,
        ):
            raise ValueError("Lineup belongs to another source or match")

        if (
            player_presence.external_match_id != summary.external_match_id
            or player_presence.external_team_id != lineup.external_team_id
        ):
            raise ValueError("Participation identity differs from lineup")

        if player_presence.periods != summary.periods:
            raise ValueError("Participation and offense must use the same periods")

        if (
            lineup.is_starter is not None
            and player_presence.is_starter is not None
            and lineup.is_starter != player_presence.is_starter
        ):
            raise ValueError("Conflicting starter classification")

        if player_presence.has_participation_evidence != (
            player_presence.elapsed_in_scope_ms is not None
        ):
            raise ValueError("Inconsistent participation evidence")

        counts: OffensiveCounts | None
        basis: Literal[
            "observed_events",
            "zero_with_presence",
            "not_applicable",
            "unknown",
        ]

        if player_summary is not None:
            if player_summary.external_team_id != lineup.external_team_id:
                raise ValueError("Offensive player belongs to another team")
            if player_presence.status != "observed_in_scope":
                raise ValueError("Offensive events without observed participation")

            counts = OffensiveCounts(
                passes_attempted=(
                    player_summary.passes.attempted if player_summary.passes is not None else 0
                ),
                passes_completed=(
                    player_summary.passes.completed if player_summary.passes is not None else 0
                ),
                shots=(player_summary.shots.shots if player_summary.shots is not None else 0),
                shots_on_target=(
                    player_summary.shots.shots_on_target if player_summary.shots is not None else 0
                ),
                goals=(player_summary.shots.goals if player_summary.shots is not None else 0),
                dribbles_attempted=(
                    player_summary.dribbles.attempted if player_summary.dribbles is not None else 0
                ),
                dribbles_completed=(
                    player_summary.dribbles.completed if player_summary.dribbles is not None else 0
                ),
            )
            basis = "observed_events"
        elif player_presence.status == "observed_in_scope":
            counts = OffensiveCounts()
            basis = "zero_with_presence"
        elif player_presence.status == "no_interval_in_scope":
            counts = None
            basis = "not_applicable"
        else:
            counts = None
            basis = "unknown"

        result.append(
            OffensiveRosterPlayer(
                lineup=lineup,
                participation=player_presence,
                observed_summary=player_summary,
                counts=counts,
                counts_basis=basis,
            )
        )

    return OffensiveRosterSummary(
        offensive_summary=summary,
        lineups_sha256=lineups_sha256,
        players=tuple(result),
    )
