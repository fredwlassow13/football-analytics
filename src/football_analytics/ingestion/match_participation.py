from pydantic import BaseModel

from football_analytics.ingestion.participation import (
    ParticipationInterval,
    ParticipationTransition,
    build_player_intervals,
)
from football_analytics.integrations.statsbomb.lineups import normalize_lineups
from football_analytics.integrations.statsbomb.participation_events import (
    normalize_participation_events,
)
from football_analytics.integrations.statsbomb.period_clock import (
    event_to_elapsed_ms,
    normalize_period_clock,
)
from football_analytics.integrations.statsbomb.starting_xi import (
    normalize_starting_xi,
)


class PlayerParticipation(BaseModel):
    external_match_id: str
    external_team_id: str
    external_player_id: str
    is_starter: bool | None
    intervals: list[ParticipationInterval]
    elapsed_on_field_ms: int | None


def reconstruct_match_participation(
    lineup_payload: object,
    events_payload: object,
    match_id: int,
) -> list[PlayerParticipation]:
    starting_xi = normalize_starting_xi(events_payload)
    lineups = normalize_lineups(
        lineup_payload,
        match_id,
        starting_xi=starting_xi,
    )
    periods = normalize_period_clock(events_payload)
    actions = normalize_participation_events(events_payload)

    roster = {item.external_player_id: item for item in lineups}

    transitions: dict[str, list[ParticipationTransition]] = {player_id: [] for player_id in roster}

    entered: set[str] = set()
    terminal_exits: set[str] = set()

    for action in sorted(actions, key=lambda item: item.event_index):
        player_id = action.external_player_id
        lineup = roster.get(player_id)

        if lineup is None:
            raise ValueError(f"Action references unlisted player {player_id}")

        if action.external_team_id != lineup.external_team_id:
            raise ValueError(f"Action references wrong team for player {player_id}")

        elapsed = event_to_elapsed_ms(
            action.period,
            action.timestamp,
            periods,
        )

        if action.reason == "Starting XI" and elapsed != 0:
            raise ValueError("Starting XI must occur at match start")

        if action.action == "enter":
            if player_id in terminal_exits:
                raise ValueError(f"Player {player_id} returned after a terminal exit")

            if action.reason == "Player On" and player_id not in entered:
                raise ValueError(f"Player {player_id} returned without an earlier entry")

            if action.reason == "Substitution" and player_id in entered:
                raise ValueError(f"Player {player_id} entered again by substitution")

            entered.add(player_id)

        elif action.reason in {
            "Substitution",
            "Red Card",
            "Second Yellow",
        }:
            terminal_exits.add(player_id)

        transitions[player_id].append(
            ParticipationTransition(
                action=action.action,
                elapsed_ms=elapsed,
                event_index=action.event_index,
            )
        )

    result: list[PlayerParticipation] = []
    match_duration = periods[-1].end_elapsed_ms

    for player_id, lineup in roster.items():
        intervals = build_player_intervals(
            transitions[player_id],
            match_duration_ms=match_duration,
        )

        duration = sum(item.duration_ms for item in intervals) if intervals else None

        result.append(
            PlayerParticipation(
                external_match_id=str(match_id),
                external_team_id=lineup.external_team_id,
                external_player_id=player_id,
                is_starter=lineup.is_starter,
                intervals=intervals,
                elapsed_on_field_ms=duration,
            )
        )

    return result
