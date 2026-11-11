from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.dribble_statistics import (
    PlayerDribbleStatistics,
    aggregate_player_dribbles,
)
from football_analytics.ingestion.offensive_scope import select_offensive_periods
from football_analytics.ingestion.pass_statistics import (
    PlayerPassStatistics,
    aggregate_player_passes,
)
from football_analytics.ingestion.shot_statistics import (
    PlayerShotStatistics,
    aggregate_player_shots,
)
from football_analytics.ingestion.shot_xg_statistics import (
    PlayerShotXgStatistics,
    aggregate_player_shot_xg,
)
from football_analytics.ingestion.team_dribble_statistics import (
    TeamDribbleStatistics,
    aggregate_team_dribbles,
)
from football_analytics.ingestion.team_pass_statistics import (
    TeamPassStatistics,
    aggregate_team_passes,
)
from football_analytics.ingestion.team_shot_statistics import (
    TeamShotStatistics,
    aggregate_team_shots,
)
from football_analytics.ingestion.team_shot_xg_statistics import (
    TeamShotXgStatistics,
    aggregate_team_shot_xg,
)
from football_analytics.integrations.base import MatchDribble, MatchPass, MatchShot


class PlayerOffensiveSummary(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_team_id: str
    external_player_id: str
    passes: PlayerPassStatistics | None
    shots: PlayerShotStatistics | None
    dribbles: PlayerDribbleStatistics | None
    xg: PlayerShotXgStatistics | None
    external_event_ids: tuple[str, ...]


class TeamOffensiveSummary(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_team_id: str
    passes: TeamPassStatistics | None
    shots: TeamShotStatistics | None
    dribbles: TeamDribbleStatistics | None
    xg: TeamShotXgStatistics | None
    external_event_ids: tuple[str, ...]


class OffensiveSummary(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    external_match_id: str = Field(min_length=1)
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    formula_version: Literal["1.0.0"] = "1.0.0"
    population_basis: Literal["observed_offensive_events"] = "observed_offensive_events"
    available_periods: tuple[int, ...]
    periods: tuple[int, ...]
    players: tuple[PlayerOffensiveSummary, ...]
    teams: tuple[TeamOffensiveSummary, ...]
    external_event_ids: tuple[str, ...]


def build_offensive_summary(
    events: Sequence[MatchPass | MatchShot | MatchDribble],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None = None,
) -> OffensiveSummary:
    if any((event.provider, event.external_match_id) != (provider, match_id) for event in events):
        raise ValueError("Events differ from requested provider and match")

    selected = select_offensive_periods(
        events,
        available_periods=available_periods,
        periods=periods,
    )

    player_teams: dict[str, str] = {}
    for event in selected:
        player_id = event.external_player_id
        team_id = event.external_team_id

        if player_id is None or team_id is None:
            raise ValueError("Offensive summary requires team and player")

        if player_id in player_teams and player_teams[player_id] != team_id:
            raise ValueError("Player belongs to multiple teams across event types")
        player_teams[player_id] = team_id

    passes = [event for event in selected if isinstance(event, MatchPass)]
    shots = [event for event in selected if isinstance(event, MatchShot)]
    dribbles = [event for event in selected if isinstance(event, MatchDribble)]

    player_passes = {item.external_player_id: item for item in aggregate_player_passes(passes)}
    player_shots = {item.external_player_id: item for item in aggregate_player_shots(shots)}
    player_dribbles = {
        item.external_player_id: item for item in aggregate_player_dribbles(dribbles)
    }
    player_xg = {item.external_player_id: item for item in aggregate_player_shot_xg(shots)}

    team_passes = {item.external_team_id: item for item in aggregate_team_passes(passes)}
    team_shots = {item.external_team_id: item for item in aggregate_team_shots(shots)}
    team_dribbles = {item.external_team_id: item for item in aggregate_team_dribbles(dribbles)}
    team_xg = {item.external_team_id: item for item in aggregate_team_shot_xg(shots)}

    return OffensiveSummary(
        provider=provider,
        external_match_id=match_id,
        events_sha256=events_sha256,
        available_periods=tuple(sorted(available_periods)),
        periods=tuple(sorted(available_periods if periods is None else periods)),
        players=tuple(
            PlayerOffensiveSummary(
                external_team_id=team_id,
                external_player_id=player_id,
                passes=player_passes.get(player_id),
                shots=player_shots.get(player_id),
                dribbles=player_dribbles.get(player_id),
                xg=player_xg.get(player_id),
                external_event_ids=tuple(
                    event.external_event_id
                    for event in selected
                    if event.external_player_id == player_id
                ),
            )
            for player_id, team_id in sorted(
                player_teams.items(),
                key=lambda item: (item[1], item[0]),
            )
        ),
        teams=tuple(
            TeamOffensiveSummary(
                external_team_id=team_id,
                passes=team_passes.get(team_id),
                shots=team_shots.get(team_id),
                dribbles=team_dribbles.get(team_id),
                xg=team_xg.get(team_id),
                external_event_ids=tuple(
                    event.external_event_id
                    for event in selected
                    if event.external_team_id == team_id
                ),
            )
            for team_id in sorted(set(player_teams.values()))
        ),
        external_event_ids=tuple(event.external_event_id for event in selected),
    )
