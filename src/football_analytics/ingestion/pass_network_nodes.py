from collections import defaultdict
from collections.abc import Sequence
from statistics import fmean
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.action_context import ContextualizedAction
from football_analytics.ingestion.pass_statistics import aggregate_player_passes
from football_analytics.integrations.base import MatchPass


class PassNetworkNode(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    provider: str
    external_match_id: str
    external_team_id: str
    external_player_id: str
    coordinate_system: Literal["statsbomb_120_80"] = "statsbomb_120_80"
    mean_x: float = Field(allow_inf_nan=False)
    mean_y: float = Field(allow_inf_nan=False)
    external_event_ids: tuple[str, ...] = Field(min_length=1)

    @property
    def pass_attempts(self) -> int:
        return len(self.external_event_ids)


def build_pass_network_nodes(
    actions: Sequence[ContextualizedAction],
) -> list[PassNetworkNode]:
    passes = [item.action for item in actions if isinstance(item.action, MatchPass)]
    validated_players = aggregate_player_passes(passes)

    if not validated_players:
        return []

    provider = validated_players[0].provider
    match_id = validated_players[0].external_match_id
    grouped: dict[
        tuple[str, str],
        list[tuple[str, float, float]],
    ] = defaultdict(list)

    for item in sorted(actions, key=lambda value: value.action.source_index):
        event = item.action

        if not isinstance(event, MatchPass):
            continue

        if event.period not in {1, 2, 3, 4}:
            continue

        team_id = event.external_team_id
        player_id = event.external_player_id
        location = item.spatial.start_location

        if team_id is None or player_id is None:
            raise ValueError("Pass network node requires team and player")

        if location is None:
            raise ValueError("Pass network node requires a start location")

        grouped[(team_id, player_id)].append((event.external_event_id, location.x, location.y))

    return [
        PassNetworkNode(
            provider=provider,
            external_match_id=match_id,
            external_team_id=team_id,
            external_player_id=player_id,
            mean_x=fmean(x for _, x, _ in samples),
            mean_y=fmean(y for _, _, y in samples),
            external_event_ids=tuple(event_id for event_id, _, _ in samples),
        )
        for (team_id, player_id), samples in sorted(grouped.items())
    ]
