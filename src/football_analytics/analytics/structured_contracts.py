"""Contracts for structured analyses; no database access or execution."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from football_analytics.analytics.contracts import Contract, SourceReference
from football_analytics.analytics.structured_registry import (
    StructuredResourceId as ResourceId,
)
from football_analytics.analytics.structured_registry import get_structured_contract
from football_analytics.ingestion.attack_sequences import AttackSequenceResult
from football_analytics.ingestion.defensive_map import DefensiveMap, DefensiveMapGroup
from football_analytics.ingestion.pass_network import PassConnection
from football_analytics.ingestion.pass_network_nodes import PassNetworkNode
from football_analytics.ingestion.possession_sequences import PossessionSequenceResult
from football_analytics.ingestion.tactical_window_catalogue import TacticalWindow


class MapOptions(Contract):
    group: DefensiveMapGroup = "all"
    columns: int = Field(default=12, ge=1, le=120)
    rows: int = Field(default=8, ge=1, le=80)


class StructuredQuery(Contract):
    contract_version: Literal["1.0.0"] = "1.0.0"
    provider: Literal["statsbomb"] = "statsbomb"
    resource_id: ResourceId
    match_id: int = Field(gt=0)
    periods: tuple[int, ...] | None = None
    team_id: str | None = None
    player_id: str | None = None
    map_options: MapOptions | None = None

    @model_validator(mode="after")
    def check_scope(self) -> Self:
        for identifier in (self.team_id, self.player_id):
            if identifier is not None and (
                not identifier.strip() or identifier != identifier.strip()
            ):
                raise ValueError("Entity IDs must be nonempty and trimmed")

        if self.periods is not None and (
            not self.periods
            or len(self.periods) != len(set(self.periods))
            or not set(self.periods).issubset({1, 2, 3, 4})
        ):
            raise ValueError("Select distinct playing periods 1–4")

        policy = get_structured_contract(self.resource_id)

        if policy.team_scope == "required" and self.team_id is None:
            raise ValueError("This resource requires a team")

        if policy.team_scope == "unsupported" and self.team_id is not None:
            raise ValueError("This resource requires the complete match context")

        if self.player_id is not None and not policy.player_filter:
            raise ValueError("Player filter is unsupported for this resource")

        if self.map_options is not None and not policy.map_options:
            raise ValueError("Map options are unsupported for this resource")

        if self.periods is not None and not policy.period_filter:
            raise ValueError("This resource currently requires all playing periods")

        return self


class NetworkData(Contract):
    resource_id: Literal["analysis.pass_connections"] = "analysis.pass_connections"
    periods: tuple[int, ...]
    nodes: tuple[PassNetworkNode, ...]
    connections: tuple[PassConnection, ...]


class MapData(Contract):
    resource_id: Literal["analysis.defensive_map"] = "analysis.defensive_map"
    value: DefensiveMap


class PossessionData(Contract):
    resource_id: Literal["analysis.possession_fragments"] = "analysis.possession_fragments"
    value: PossessionSequenceResult


class AttackData(Contract):
    resource_id: Literal["analysis.attacks"] = "analysis.attacks"
    value: AttackSequenceResult


class WindowsData(Contract):
    resource_id: Literal["analysis.tactical_windows"] = "analysis.tactical_windows"
    windows: tuple[TacticalWindow, ...]


StructuredData = Annotated[
    NetworkData | MapData | PossessionData | AttackData | WindowsData,
    Field(discriminator="resource_id"),
]


class StructuredResult(Contract):
    contract_version: Literal["1.0.0"] = "1.0.0"
    query: StructuredQuery
    status: Literal["available", "review", "unavailable"]
    source: SourceReference | None = None
    data: StructuredData | None = None
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def check_result(self) -> Self:
        if any(not reason.strip() for reason in self.reasons):
            raise ValueError("Reasons must be nonempty")

        if self.status != "available" and not self.reasons:
            raise ValueError("Review/unavailable result requires reasons")

        if self.source is not None and self.source.match_id != self.query.match_id:
            raise ValueError("Source belongs to another match")

        if self.status == "unavailable":
            if self.data is not None:
                raise ValueError("Unavailable result must not contain data")
            return self

        if self.source is None or self.data is None:
            raise ValueError("Available/review result requires source and data")

        if self.data.resource_id != self.query.resource_id:
            raise ValueError("Payload differs from requested resource")

        selected = self.query.periods or self.source.available_periods
        if not set(selected).issubset(self.source.available_periods):
            raise ValueError("Requested period is unavailable")

        review = False

        if isinstance(self.data, NetworkData):
            if set(self.data.periods) != set(selected) or len(self.data.periods) != len(selected):
                raise ValueError("Network periods differ from requested scope")

            nodes = {node.external_player_id: node for node in self.data.nodes}
            if len(nodes) != len(self.data.nodes):
                raise ValueError("Duplicate network node")

            node_events: set[str] = set()
            for node in self.data.nodes:
                ids = node.external_event_ids
                if len(ids) != len(set(ids)) or node_events.intersection(ids):
                    raise ValueError("Duplicate network origin event")
                node_events.update(ids)

            edges = set()
            event_ids: set[str] = set()
            items: tuple[PassNetworkNode | PassConnection, ...] = (
                *self.data.nodes,
                *self.data.connections,
            )

            for item in items:
                if (item.provider, item.external_match_id, item.external_team_id) != (
                    self.query.provider,
                    str(self.query.match_id),
                    self.query.team_id,
                ):
                    raise ValueError("Network identity differs from query")

            for edge in self.data.connections:
                key = (edge.external_sender_player_id, edge.external_recipient_player_id)

                if key in edges or len(edge.external_event_ids) != len(
                    set(edge.external_event_ids)
                ):
                    raise ValueError("Duplicate network connection or event")

                if event_ids.intersection(edge.external_event_ids):
                    raise ValueError("Pass event appears in multiple connections")

                edges.add(key)
                event_ids.update(edge.external_event_ids)

                sender = nodes.get(edge.external_sender_player_id)
                if sender is None or not set(edge.external_event_ids).issubset(
                    sender.external_event_ids
                ):
                    raise ValueError("Connection lacks its sender origin evidence")

                review |= edge.external_recipient_player_id not in nodes

        elif isinstance(self.data, WindowsData):
            keys = set()

            for window in self.data.windows:
                if (window.provider, window.external_match_id) != (
                    self.query.provider,
                    str(self.query.match_id),
                ) or window.period not in selected:
                    raise ValueError("Window identity/scope differs from query")

                window_key = (window.period, window.number_in_period)
                if window_key in keys:
                    raise ValueError("Duplicate tactical window")
                keys.add(window_key)

            if {window.period for window in self.data.windows} != set(selected):
                raise ValueError("Windows must cover every selected period")

        else:
            value = self.data.value

            if (value.provider, value.external_match_id, value.events_sha256) != (
                self.query.provider,
                str(self.query.match_id),
                self.source.events_sha256,
            ) or value.available_periods != self.source.available_periods:
                raise ValueError("Payload identity/source differs from provenance")

            if isinstance(value, DefensiveMap):
                options = self.query.map_options or MapOptions()

                if (value.external_team_id, value.external_player_id) != (
                    self.query.team_id,
                    self.query.player_id,
                ) or set(value.periods) != set(selected):
                    raise ValueError("Map selection differs from query")

                if (value.group, value.columns, value.rows) != (
                    options.group,
                    options.columns,
                    options.rows,
                ) or value.event_types is not None:
                    raise ValueError("Map options differ from query")

                review = bool(value.location_issues)

            else:
                review = bool(value.issues)

                if isinstance(self.data, AttackData):
                    review |= any(a.status == "review" for a in self.data.value.attacks)
                    review |= any(s.status == "review" for s in self.data.value.stoppages)

        if review and self.status != "review":
            raise ValueError("Payload diagnostics require review status")

        return self
