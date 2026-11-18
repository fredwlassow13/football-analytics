from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.aerial_statistics import (
    PlayerAerialStatistics,
    aggregate_player_aerials,
)
from football_analytics.ingestion.defensive_scope import (
    DefensiveEvent,
    select_defensive_periods,
)
from football_analytics.ingestion.defensive_statistics import (
    PlayerDefensiveStatistics,
    aggregate_player_defensive_actions,
)
from football_analytics.ingestion.duel_statistics import (
    PlayerDuelStatistics,
    aggregate_player_duels,
)
from football_analytics.ingestion.team_aerial_statistics import (
    TeamAerialStatistics,
    aggregate_team_aerials,
)
from football_analytics.ingestion.team_defensive_statistics import (
    TeamDefensiveStatistics,
    aggregate_team_defensive_actions,
)
from football_analytics.ingestion.team_duel_statistics import (
    TeamDuelStatistics,
    aggregate_team_duels,
)
from football_analytics.integrations.base import (
    MatchAerialDuel,
    MatchClearance,
    MatchDuel,
    MatchEvent,
)


class PlayerDefensiveSummary(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_team_id: str
    external_player_id: str
    actions: PlayerDefensiveStatistics | None
    duels: PlayerDuelStatistics | None
    aerials: PlayerAerialStatistics | None
    external_event_ids: tuple[str, ...]


class TeamDefensiveSummary(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_team_id: str
    actions: TeamDefensiveStatistics | None
    duels: TeamDuelStatistics | None
    aerials: TeamAerialStatistics | None
    external_event_ids: tuple[str, ...]


class DefensiveSummary(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    external_match_id: str = Field(min_length=1)
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    formula_version: Literal["1.0.0"] = "1.0.0"
    population_basis: Literal["observed_action_and_aerial_sources"] = (
        "observed_action_and_aerial_sources"
    )
    available_periods: tuple[int, ...]
    periods: tuple[int, ...]
    players: tuple[PlayerDefensiveSummary, ...]
    teams: tuple[TeamDefensiveSummary, ...]
    action_event_ids: tuple[str, ...]
    aerial_event_ids: tuple[str, ...]
    shared_event_ids: tuple[str, ...]
    external_event_ids: tuple[str, ...]


def _select_aerials(
    aerials: Sequence[MatchAerialDuel],
    *,
    provider: str,
    match_id: str,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...],
) -> list[MatchAerialDuel]:
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    selected: list[MatchAerialDuel] = []

    for event in aerials:
        if not isinstance(event, MatchAerialDuel):
            raise ValueError("Unsupported aerial event type")

        if (event.provider, event.external_match_id) != (provider, match_id):
            raise ValueError("Aerials differ from requested provider and match")

        if event.external_event_id in seen_ids or event.source_index in seen_indexes:
            raise ValueError("Duplicate aerial source event")

        seen_ids.add(event.external_event_id)
        seen_indexes.add(event.source_index)

        if event.period not in {*available_periods, 5}:
            raise ValueError("Aerial period is incompatible with available periods")

        if event.period in periods:
            selected.append(event)

    return sorted(selected, key=lambda item: item.source_index)


def _validate_shared_evidence(
    actions: Sequence[DefensiveEvent],
    aerials: Sequence[MatchAerialDuel],
) -> None:
    actions_by_id = {event.external_event_id: event for event in actions}
    aerials_by_id = {event.external_event_id: event for event in aerials}

    for action in actions:
        requires_projection = (
            isinstance(action, MatchClearance) and action.aerial_won is True
        ) or (isinstance(action, MatchDuel) and action.duel_type == "aerial_lost")

        if requires_projection and action.external_event_id not in aerials_by_id:
            raise ValueError("Missing aerial projection for action source")

    for aerial in aerials:
        counterpart = actions_by_id.get(aerial.external_event_id)

        if aerial.event_type == "Clearance":
            if not (
                isinstance(counterpart, MatchClearance)
                and counterpart.aerial_won is True
                and aerial.outcome == "won"
            ):
                raise ValueError("Aerial clearance evidence differs")

        elif aerial.event_type == "Duel":
            if not (
                isinstance(counterpart, MatchDuel)
                and counterpart.duel_type == "aerial_lost"
                and counterpart.outcome in {None, "lost"}
                and aerial.outcome == "lost"
            ):
                raise ValueError("Aerial duel evidence differs")

        elif counterpart is not None:
            raise ValueError("Unexpected shared aerial source type")


def build_defensive_summary(
    events: Sequence[DefensiveEvent],
    aerials: Sequence[MatchAerialDuel],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None = None,
) -> DefensiveSummary:
    selected = select_defensive_periods(
        events,
        available_periods=available_periods,
        periods=periods,
    )

    if any((event.provider, event.external_match_id) != (provider, match_id) for event in events):
        raise ValueError("Actions differ from requested provider and match")

    selected_periods = tuple(sorted(available_periods if periods is None else periods))
    selected_aerials = _select_aerials(
        aerials,
        provider=provider,
        match_id=match_id,
        available_periods=available_periods,
        periods=selected_periods,
    )

    _validate_shared_evidence(selected, selected_aerials)

    sources: dict[str, MatchEvent] = {}
    indexes: dict[int, str] = {}
    header_fields = set(MatchEvent.model_fields)

    for event in [*selected, *selected_aerials]:
        previous = sources.get(event.external_event_id)

        if previous is not None:
            if previous.model_dump(include=header_fields) != event.model_dump(
                include=header_fields
            ):
                raise ValueError("Shared source headers differ")

        indexed_id = indexes.get(event.source_index)
        if indexed_id is not None and indexed_id != event.external_event_id:
            raise ValueError("Source index identifies different events")

        sources[event.external_event_id] = event
        indexes[event.source_index] = event.external_event_id

    ordered_sources = sorted(sources.values(), key=lambda item: item.source_index)
    player_teams: dict[str, str] = {}

    for source_event in ordered_sources:
        player_id = source_event.external_player_id
        team_id = source_event.external_team_id

        if player_id is None or team_id is None:
            raise ValueError("Defensive summary requires team and player")

        if player_id in player_teams and player_teams[player_id] != team_id:
            raise ValueError("Player belongs to multiple teams across views")

        player_teams[player_id] = team_id

    duels = [event for event in selected if isinstance(event, MatchDuel)]

    player_actions = {
        item.external_player_id: item
        for item in aggregate_player_defensive_actions(
            selected,
            available_periods=available_periods,
            periods=selected_periods,
        )
    }
    team_actions = {
        item.external_team_id: item
        for item in aggregate_team_defensive_actions(
            selected,
            available_periods=available_periods,
            periods=selected_periods,
        )
    }
    player_duels = {item.external_player_id: item for item in aggregate_player_duels(duels)}
    team_duels = {item.external_team_id: item for item in aggregate_team_duels(duels)}
    player_aerials = {
        item.external_player_id: item for item in aggregate_player_aerials(selected_aerials)
    }
    team_aerials = {
        item.external_team_id: item for item in aggregate_team_aerials(selected_aerials)
    }

    action_ids = {event.external_event_id for event in selected}
    aerial_ids = {event.external_event_id for event in selected_aerials}

    return DefensiveSummary(
        provider=provider,
        external_match_id=match_id,
        events_sha256=events_sha256,
        available_periods=tuple(sorted(available_periods)),
        periods=selected_periods,
        players=tuple(
            PlayerDefensiveSummary(
                external_team_id=team_id,
                external_player_id=player_id,
                actions=player_actions.get(player_id),
                duels=player_duels.get(player_id),
                aerials=player_aerials.get(player_id),
                external_event_ids=tuple(
                    event.external_event_id
                    for event in ordered_sources
                    if event.external_player_id == player_id
                ),
            )
            for player_id, team_id in sorted(
                player_teams.items(),
                key=lambda item: (item[1], item[0]),
            )
        ),
        teams=tuple(
            TeamDefensiveSummary(
                external_team_id=team_id,
                actions=team_actions.get(team_id),
                duels=team_duels.get(team_id),
                aerials=team_aerials.get(team_id),
                external_event_ids=tuple(
                    event.external_event_id
                    for event in ordered_sources
                    if event.external_team_id == team_id
                ),
            )
            for team_id in sorted(set(player_teams.values()))
        ),
        action_event_ids=tuple(event.external_event_id for event in selected),
        aerial_event_ids=tuple(event.external_event_id for event in selected_aerials),
        shared_event_ids=tuple(
            event.external_event_id
            for event in ordered_sources
            if event.external_event_id in action_ids & aerial_ids
        ),
        external_event_ids=tuple(event.external_event_id for event in ordered_sources),
    )
