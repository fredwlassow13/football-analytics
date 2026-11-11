from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.tactical_window_catalogue import TacticalWindow
from football_analytics.integrations.base import MatchLineup
from football_analytics.integrations.statsbomb.participation_events import PlayerAction

PERMANENT_REASONS = {"Substitution", "Red Card", "Second Yellow"}


class WindowRosterPlayer(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    external_player_id: str
    shirt_number: int | None = Field(default=None, ge=0)
    is_match_starter: bool


class WindowTeamLineup(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    window: TacticalWindow
    external_team_id: str
    players: tuple[WindowRosterPlayer, ...] = Field(max_length=11)


def build_window_lineups(
    lineups: Sequence[MatchLineup],
    participation_actions: Sequence[PlayerAction],
    windows: Sequence[TacticalWindow],
) -> list[WindowTeamLineup]:
    """Build nominal lineups from previously validated participation sources."""

    if not windows:
        return []

    identities = {(window.provider, window.external_match_id) for window in windows}

    if len(identities) != 1:
        raise ValueError("Windows must share provider and match")

    identity = next(iter(identities))

    if not lineups:
        raise ValueError("Match lineups are required")

    if any((player.provider, player.external_match_id) != identity for player in lineups):
        raise ValueError("Lineups and windows must share provider and match")

    if any(player.is_starter is None for player in lineups):
        raise ValueError("Starter classification is required")

    by_player = {player.external_player_id: player for player in lineups}

    if len(by_player) != len(lineups):
        raise ValueError("Repeated player in match lineups")

    team_ids = {player.external_team_id for player in lineups}

    if len(team_ids) != 2:
        raise ValueError("Expected two team rosters")

    rosters = {
        team_id: {
            player.external_player_id
            for player in lineups
            if player.external_team_id == team_id and player.is_starter is True
        }
        for team_id in team_ids
    }

    if any(not roster for roster in rosters.values()):
        raise ValueError("Each team requires known starters")

    ordered_windows = sorted(
        windows,
        key=lambda window: window.start_source_index,
    )

    for previous, current in zip(
        ordered_windows,
        ordered_windows[1:],
        strict=False,
    ):
        if current.start_source_index < previous.end_source_index:
            raise ValueError("Overlapping source windows")

    changes = sorted(
        (action for action in participation_actions if action.reason in PERMANENT_REASONS),
        key=lambda action: action.event_index,
    )
    change_position = 0
    result: list[WindowTeamLineup] = []

    for window in ordered_windows:
        while (
            change_position < len(changes)
            and changes[change_position].event_index <= window.start_source_index
        ):
            change = changes[change_position]
            player = by_player.get(change.external_player_id)

            if player is None:
                raise ValueError("Change references an unlisted player")

            if player.external_team_id != change.external_team_id:
                raise ValueError("Change references the wrong team")

            roster = rosters[change.external_team_id]

            if change.reason in {"Red Card", "Second Yellow"} and change.action != "leave":
                raise ValueError("Dismissal must be a leave action")

            if change.action == "leave":
                if change.external_player_id not in roster:
                    raise ValueError("Permanent exit without nominal presence")
                roster.remove(change.external_player_id)
            else:
                if change.external_player_id in roster:
                    raise ValueError("Entry while already in nominal lineup")
                roster.add(change.external_player_id)

            change_position += 1

        if (
            change_position < len(changes)
            and changes[change_position].event_index < window.end_source_index
        ):
            raise ValueError("Window spans a permanent lineup change")

        for team_id in sorted(team_ids):
            player_ids = sorted(
                rosters[team_id],
                key=lambda player_id: (
                    by_player[player_id].shirt_number is None,
                    (
                        by_player[player_id].shirt_number
                        if by_player[player_id].shirt_number is not None
                        else 0
                    ),
                    player_id,
                ),
            )

            result.append(
                WindowTeamLineup(
                    window=window,
                    external_team_id=team_id,
                    players=tuple(
                        WindowRosterPlayer(
                            external_player_id=player_id,
                            shirt_number=by_player[player_id].shirt_number,
                            is_match_starter=(by_player[player_id].is_starter is True),
                        )
                        for player_id in player_ids
                    ),
                )
            )

    return result
