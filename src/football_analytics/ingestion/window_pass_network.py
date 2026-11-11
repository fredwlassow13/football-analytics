from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.action_context import ContextualizedAction
from football_analytics.ingestion.pass_network import (
    PassConnection,
    build_pass_connections,
)
from football_analytics.ingestion.pass_network_nodes import (
    PassNetworkNode,
    build_pass_network_nodes,
)
from football_analytics.ingestion.pass_network_window import (
    PassNetworkWindow,
    select_pass_network_window,
)
from football_analytics.ingestion.tactical_window_catalogue import TacticalWindow
from football_analytics.ingestion.tactical_window_lineups import WindowTeamLineup
from football_analytics.integrations.base import MatchPass


class WindowPassNetwork(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    lineup: WindowTeamLineup
    nodes: tuple[PassNetworkNode, ...]
    connections: tuple[PassConnection, ...]

    @property
    def pass_attempts(self) -> int:
        return sum(node.pass_attempts for node in self.nodes)

    @property
    def completed_passes(self) -> int:
        return sum(connection.completed_passes for connection in self.connections)

    @property
    def players_without_origins(self) -> tuple[str, ...]:
        positioned = {node.external_player_id for node in self.nodes}
        return tuple(
            player.external_player_id
            for player in self.lineup.players
            if player.external_player_id not in positioned
        )

    @property
    def drawable_connections(self) -> tuple[PassConnection, ...]:
        positioned = {node.external_player_id for node in self.nodes}
        return tuple(
            connection
            for connection in self.connections
            if connection.external_sender_player_id in positioned
            and connection.external_recipient_player_id in positioned
        )

    @property
    def completed_passes_without_positions(self) -> int:
        return self.completed_passes - sum(
            connection.completed_passes for connection in self.drawable_connections
        )

    @property
    def status(
        self,
    ) -> Literal[
        "zero_duration",
        "no_passes",
        "no_completed_passes",
        "no_positioned_connections",
        "partial_positions",
        "complete_positions",
    ]:
        if self.lineup.window.duration_ms == 0:
            return "zero_duration"
        if self.pass_attempts == 0:
            return "no_passes"
        if self.completed_passes == 0:
            return "no_completed_passes"
        if not self.drawable_connections:
            return "no_positioned_connections"
        if self.players_without_origins:
            return "partial_positions"
        return "complete_positions"


def prepare_window_pass_networks(
    actions: Sequence[ContextualizedAction],
    window_lineups: Sequence[WindowTeamLineup],
) -> list[WindowPassNetwork]:
    """Prepare networks from a validated catalogue and participation sources."""

    if not window_lineups:
        return []

    windows: dict[tuple[int, int], TacticalWindow] = {}
    rosters: dict[
        tuple[int, int],
        dict[str, WindowTeamLineup],
    ] = defaultdict(dict)

    for lineup in window_lineups:
        window = lineup.window
        key = (window.period, window.number_in_period)

        if key in windows and windows[key] != window:
            raise ValueError("Conflicting definitions for the same window")

        if lineup.external_team_id in rosters[key]:
            raise ValueError("Duplicate team lineup for window")

        windows[key] = window
        rosters[key][lineup.external_team_id] = lineup

    ordered_keys = sorted(
        windows,
        key=lambda key: windows[key].start_source_index,
    )

    for previous, current in zip(
        ordered_keys,
        ordered_keys[1:],
        strict=False,
    ):
        if windows[current].start_source_index < windows[previous].end_source_index:
            raise ValueError("Overlapping source windows")

    result: list[WindowPassNetwork] = []

    for key in ordered_keys:
        window = windows[key]
        selected = select_pass_network_window(
            actions,
            PassNetworkWindow(
                provider=window.provider,
                external_match_id=window.external_match_id,
                label=f"P{window.period}, trecho {window.number_in_period}",
                start_source_index=window.start_source_index,
                end_source_index=window.end_source_index,
            ),
        )
        passes = [item.action for item in selected if isinstance(item.action, MatchPass)]
        player_ids = {
            team_id: {player.external_player_id for player in lineup.players}
            for team_id, lineup in rosters[key].items()
        }

        for event in passes:
            team_id = event.external_team_id

            if (
                team_id is None
                or team_id not in player_ids
                or event.external_player_id not in player_ids[team_id]
            ):
                raise ValueError("Pass sender outside nominal lineup")

            if (
                event.is_completed is True
                and event.external_recipient_player_id not in player_ids[team_id]
            ):
                raise ValueError("Completed pass recipient outside nominal lineup")

        nodes = build_pass_network_nodes(selected)
        connections = build_pass_connections(passes)

        for team_id, lineup in sorted(rosters[key].items()):
            result.append(
                WindowPassNetwork(
                    lineup=lineup,
                    nodes=tuple(node for node in nodes if node.external_team_id == team_id),
                    connections=tuple(
                        connection
                        for connection in connections
                        if connection.external_team_id == team_id
                    ),
                )
            )

    return result
