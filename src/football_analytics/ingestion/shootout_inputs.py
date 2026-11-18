from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.match_end_presence import (
    MatchEndPresence,
    classify_match_end_presence,
)
from football_analytics.ingestion.match_participation import (
    PlayerParticipation,
)
from football_analytics.ingestion.participation import (
    ParticipationTransition,
)
from football_analytics.ingestion.shootout_eligibility import (
    ShootoutEligibilityContext,
)
from football_analytics.integrations.statsbomb.participation_events import (
    PlayerAction,
)
from football_analytics.integrations.statsbomb.period_clock import (
    PeriodBoundary,
    event_to_elapsed_ms,
)


class ShootoutPreparedPlayer(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    presence: MatchEndPresence
    eligibility_context: ShootoutEligibilityContext


def prepare_shootout_inputs(
    players: Sequence[PlayerParticipation],
    actions: Sequence[PlayerAction],
    periods: Sequence[PeriodBoundary],
    *,
    match_id: str,
) -> tuple[ShootoutPreparedPlayer, ...]:
    ordered_periods = sorted(periods, key=lambda item: item.period)

    if len({period.period for period in ordered_periods}) != len(ordered_periods) or {
        period.period for period in ordered_periods
    } not in ({1, 2}, {1, 2, 3, 4}):
        raise ValueError("Clock must describe two or four complete periods")

    previous_end = 0
    for period in ordered_periods:
        if (
            period.duration_ms <= 0
            or period.start_elapsed_ms != previous_end
            or period.end_elapsed_ms != period.start_elapsed_ms + period.duration_ms
        ):
            raise ValueError("Invalid period boundaries")
        previous_end = period.end_elapsed_ms

    roster = {player.external_player_id: player for player in players}

    if len(roster) != len(players):
        raise ValueError("Duplicate participation player")

    if any(player.external_match_id != match_id for player in players):
        raise ValueError("Participation belongs to another match")

    grouped: dict[str, list[PlayerAction]] = {player_id: [] for player_id in roster}

    for action in actions:
        player = roster.get(action.external_player_id)

        if player is None:
            raise ValueError("Action references an unlisted player")

        if action.external_team_id != player.external_team_id:
            raise ValueError("Action team differs from participation")

        if action.period not in {period.period for period in ordered_periods}:
            raise ValueError("Participation action is outside playing periods")

        grouped[action.external_player_id].append(action)

    result: list[ShootoutPreparedPlayer] = []

    for player_id, player in sorted(
        roster.items(),
        key=lambda pair: (pair[1].external_team_id, pair[0]),
    ):
        player_actions = sorted(
            grouped[player_id],
            key=lambda item: item.event_index,
        )

        transitions = [
            ParticipationTransition(
                action=action.action,
                elapsed_ms=event_to_elapsed_ms(
                    action.period,
                    action.timestamp,
                    ordered_periods,
                ),
                event_index=action.event_index,
            )
            for action in player_actions
        ]

        presence = classify_match_end_presence(
            player,
            transitions,
            match_duration_ms=previous_end,
        )

        last_exit_reason = (
            player_actions[-1].reason if player_actions and presence.status == "inactive" else None
        )

        sent_off = any(action.reason in {"Red Card", "Second Yellow"} for action in player_actions)

        result.append(
            ShootoutPreparedPlayer(
                presence=presence,
                eligibility_context=ShootoutEligibilityContext(
                    sent_off=True if sent_off else None,
                    last_exit_reason=last_exit_reason,
                ),
            )
        )

    return tuple(result)
