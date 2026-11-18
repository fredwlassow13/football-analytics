from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.defensive_statistics import FLAG_FIELDS
from football_analytics.ingestion.defensive_summary import (
    DefensiveSummary,
    PlayerDefensiveSummary,
)
from football_analytics.ingestion.player_scope_participation import (
    PlayerScopeParticipation,
)
from football_analytics.integrations.base import MatchLineup


class DefensiveCounts(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    unique_source_events: int = Field(default=0, ge=0)
    action_events: int = Field(default=0, ge=0)
    duel_events: int = Field(default=0, ge=0)
    aerial_results: int = Field(default=0, ge=0)
    aerial_won: int = Field(default=0, ge=0)
    aerial_lost: int = Field(default=0, ge=0)
    shared_source_events: int = Field(default=0, ge=0)
    actions_by_type: dict[str, int] = Field(default_factory=lambda: dict.fromkeys(FLAG_FIELDS, 0))


class DefensiveRosterPlayer(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    lineup: MatchLineup
    participation: PlayerScopeParticipation
    observed_summary: PlayerDefensiveSummary | None
    counts: DefensiveCounts | None
    counts_basis: Literal[
        "observed_events",
        "zero_with_presence",
        "not_applicable",
        "unknown",
    ]


class DefensiveRosterSummary(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    defensive_summary: DefensiveSummary
    lineups_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    population_basis: Literal["listed_players"] = "listed_players"
    policy_version: Literal["1.0.0"] = "1.0.0"
    participation_metric: Literal["elapsed_on_field_ms"] = "elapsed_on_field_ms"
    participation_formula_version: Literal["1.0.0"] = "1.0.0"
    participation_events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    players: tuple[DefensiveRosterPlayer, ...]


def build_defensive_roster(
    summary: DefensiveSummary,
    lineups: Sequence[MatchLineup],
    participation: Sequence[PlayerScopeParticipation],
    *,
    lineups_sha256: str,
    participation_events_sha256: str,
) -> DefensiveRosterSummary:
    if participation_events_sha256 != summary.events_sha256:
        raise ValueError("Participation and defense event source hashes differ")
    roster = {item.external_player_id: item for item in lineups}
    presence = {item.external_player_id: item for item in participation}
    observed = {item.external_player_id: item for item in summary.players}

    if len(roster) != len(lineups):
        raise ValueError("Duplicate lineup player")
    if len(presence) != len(participation):
        raise ValueError("Duplicate participation player")
    if len(observed) != len(summary.players):
        raise ValueError("Duplicate defensive player")
    if set(roster) != set(presence):
        raise ValueError("Participation must cover exactly the listed players")
    if not set(observed).issubset(roster):
        raise ValueError("Defensive summary contains an unlisted player")

    source_ids = [key for player in summary.players for key in player.external_event_ids]
    if len(source_ids) != len(set(source_ids)) or set(source_ids) != set(
        summary.external_event_ids
    ):
        raise ValueError("Defensive roster sources differ from summary")

    result: list[DefensiveRosterPlayer] = []

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
            raise ValueError("Participation and defense must use the same periods")

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

        _validate_presence(player_presence)

        counts: DefensiveCounts | None
        basis: Literal[
            "observed_events",
            "zero_with_presence",
            "not_applicable",
            "unknown",
        ]

        if player_summary is not None:
            if player_summary.external_team_id != lineup.external_team_id:
                raise ValueError("Defensive player belongs to another team")
            if player_presence.status != "observed_in_scope":
                raise ValueError("Defensive events without observed participation")

            counts = _observed_counts(player_summary, summary)
            basis = "observed_events"
        elif player_presence.status == "observed_in_scope":
            counts = DefensiveCounts()
            basis = "zero_with_presence"
        elif player_presence.status == "no_interval_in_scope":
            counts = None
            basis = "not_applicable"
        else:
            counts = None
            basis = "unknown"

        result.append(
            DefensiveRosterPlayer(
                lineup=lineup,
                participation=player_presence,
                observed_summary=player_summary,
                counts=counts,
                counts_basis=basis,
            )
        )

    return DefensiveRosterSummary(
        defensive_summary=summary,
        lineups_sha256=lineups_sha256,
        players=tuple(result),
        participation_events_sha256=participation_events_sha256,
    )


def _validate_presence(presence: PlayerScopeParticipation) -> None:
    duration = presence.elapsed_in_scope_ms
    if duration is None:
        if presence.intervals or presence.has_participation_evidence:
            raise ValueError("Unknown participation has interval evidence")
        return
    previous_end = -1
    for interval in sorted(presence.intervals, key=lambda item: item.start_ms):
        if (
            interval.start_ms < 0
            or interval.start_ms < previous_end
            or interval.end_ms <= interval.start_ms
        ):
            raise ValueError("Invalid scoped participation intervals")
        previous_end = interval.end_ms
    if duration < 0 or duration != sum(interval.duration_ms for interval in presence.intervals):
        raise ValueError("Scoped participation duration differs from intervals")


def _observed_counts(player: PlayerDefensiveSummary, summary: DefensiveSummary) -> DefensiveCounts:
    if not player.external_event_ids:
        raise ValueError("Observed defensive player has no source events")
    for part in (player.actions, player.duels, player.aerials):
        if part is not None and len(part.external_event_ids) != len(set(part.external_event_ids)):
            raise ValueError("Duplicate nested defensive source IDs")
        if part is not None and (
            part.provider,
            part.external_match_id,
            part.external_team_id,
            part.external_player_id,
        ) != (
            summary.provider,
            summary.external_match_id,
            player.external_team_id,
            player.external_player_id,
        ):
            raise ValueError("Nested defensive identity differs")
    if player.actions is not None and player.actions.periods != summary.periods:
        raise ValueError("Nested defensive periods differ")
    action_ids = set(player.actions.external_event_ids) if player.actions else set()
    aerial_ids = set(player.aerials.external_event_ids) if player.aerials else set()
    duel_ids = set(player.duels.external_event_ids) if player.duels else set()
    if (
        len(player.external_event_ids) != len(set(player.external_event_ids))
        or action_ids | aerial_ids != set(player.external_event_ids)
        or not duel_ids.issubset(action_ids)
    ):
        raise ValueError("Defensive player source population differs")
    counts_by_type = dict.fromkeys(FLAG_FIELDS, 0)
    if player.actions is not None:
        for action in player.actions.actions:
            if action.event_type not in counts_by_type or counts_by_type[action.event_type] != 0:
                raise ValueError("Invalid defensive action categories")
            counts_by_type[action.event_type] = action.event_count
        if sum(counts_by_type.values()) != len(action_ids) or player.actions.event_count != len(
            action_ids
        ):
            raise ValueError("Defensive action counts differ from sources")
    if player.duels is not None and player.duels.total_duel_events != len(duel_ids):
        raise ValueError("Defensive duel counts differ from sources")
    if player.aerials is not None and player.aerials.contested != len(aerial_ids):
        raise ValueError("Defensive aerial counts differ from sources")
    return DefensiveCounts(
        unique_source_events=len(player.external_event_ids),
        action_events=len(action_ids),
        duel_events=len(duel_ids),
        aerial_results=len(aerial_ids),
        aerial_won=player.aerials.won if player.aerials else 0,
        aerial_lost=player.aerials.lost if player.aerials else 0,
        shared_source_events=len(action_ids & aerial_ids),
        actions_by_type=counts_by_type,
    )
