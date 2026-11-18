"""Catalogue and filter policies for the initial structured contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.analytics.resources import RESOURCES, AnalysisResource

StructuredResourceId = Literal[
    "analysis.pass_connections",
    "analysis.defensive_map",
    "analysis.possession_fragments",
    "analysis.attacks",
    "analysis.tactical_windows",
]


class StructuredContractDefinition(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    resource_id: StructuredResourceId
    team_scope: Literal["required", "unsupported"]
    player_filter: bool = False
    period_filter: bool = True
    map_options: bool = False


STRUCTURED_CONTRACTS = (
    StructuredContractDefinition(
        resource_id="analysis.pass_connections",
        team_scope="required",
    ),
    StructuredContractDefinition(
        resource_id="analysis.defensive_map",
        team_scope="required",
        player_filter=True,
        map_options=True,
    ),
    StructuredContractDefinition(
        resource_id="analysis.possession_fragments",
        team_scope="unsupported",
        period_filter=False,
    ),
    StructuredContractDefinition(
        resource_id="analysis.attacks",
        team_scope="unsupported",
        period_filter=False,
    ),
    StructuredContractDefinition(
        resource_id="analysis.tactical_windows",
        team_scope="unsupported",
    ),
)


def get_structured_contract(resource_id: str) -> StructuredContractDefinition:
    for definition in STRUCTURED_CONTRACTS:
        if definition.resource_id == resource_id:
            return definition
    raise ValueError(f"Structured contract is not registered: {resource_id}")


def get_structured_resource(resource_id: str) -> AnalysisResource:
    get_structured_contract(resource_id)

    for resource in RESOURCES:
        if resource.id == resource_id:
            if not resource.contract_available:
                raise ValueError("Resource has no registered contract metadata")
            return resource

    raise ValueError(f"Resource is missing from catalogue: {resource_id}")
