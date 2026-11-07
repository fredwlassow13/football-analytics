from collections import defaultdict

from pydantic import BaseModel

from football_analytics.ingestion.match_participation import (
    PlayerParticipation,
)


class TeamOccupancyInterval(BaseModel):
    external_team_id: str
    start_ms: int
    end_ms: int
    players_on_field: int


def build_team_occupancy(
    players: list[PlayerParticipation],
    match_duration_ms: int,
) -> list[TeamOccupancyInterval]:
    if match_duration_ms <= 0:
        raise ValueError("Match duration must be positive")

    match_ids = {player.external_match_id for player in players}

    if len(match_ids) != 1:
        raise ValueError("Expected players from exactly one match")

    player_ids = [player.external_player_id for player in players]

    if len(player_ids) != len(set(player_ids)):
        raise ValueError("Repeated player in match participation")

    team_ids = {player.external_team_id for player in players}

    if len(team_ids) != 2:
        raise ValueError("Expected exactly two teams")

    result: list[TeamOccupancyInterval] = []

    for team_id in sorted(team_ids):
        changes: dict[int, int] = defaultdict(int)
        changes[0] = 0
        changes[match_duration_ms] = 0

        for player in players:
            if player.external_team_id != team_id:
                continue

            previous_end = 0

            for interval in sorted(
                player.intervals,
                key=lambda item: item.start_ms,
            ):
                if not (0 <= interval.start_ms <= interval.end_ms <= match_duration_ms):
                    raise ValueError("Invalid participation interval")

                if interval.start_ms < previous_end:
                    raise ValueError("Overlapping intervals for player")

                previous_end = interval.end_ms
                changes[interval.start_ms] += 1
                changes[interval.end_ms] -= 1

        times = sorted(changes)
        count = 0
        team_result: list[TeamOccupancyInterval] = []

        for position, start in enumerate(times):
            # Aplica juntas as entradas e saídas no mesmo instante.
            count += changes[start]

            if count < 0 or count > 11:
                raise ValueError(f"Invalid player count for team {team_id}: {count}")

            if position == len(times) - 1:
                if count != 0:
                    raise ValueError("Unclosed team participation")
                break

            end = times[position + 1]

            if (
                team_result
                and team_result[-1].players_on_field == count
                and team_result[-1].end_ms == start
            ):
                team_result[-1].end_ms = end
            else:
                team_result.append(
                    TeamOccupancyInterval(
                        external_team_id=team_id,
                        start_ms=start,
                        end_ms=end,
                        players_on_field=count,
                    )
                )

        result.extend(team_result)

    return result
