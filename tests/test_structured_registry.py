import importlib
from typing import get_args

import pytest
from pydantic import ValidationError

from football_analytics.analytics.catalogue import get_catalogue_entry
from football_analytics.analytics.resources import RESOURCES, AnalysisResource
from football_analytics.analytics.structured_contracts import (
    StructuredQuery,
    StructuredResult,
)
from football_analytics.analytics.structured_registry import (
    STRUCTURED_CONTRACTS,
    StructuredResourceId,
    get_structured_contract,
    get_structured_resource,
)


def resolve(path):
    module, name = path.rsplit(".", 1)
    return getattr(importlib.import_module(module), name)


def test_registry_covers_literal_and_catalogue_without_duplicates():
    ids = [entry.resource_id for entry in STRUCTURED_CONTRACTS]

    assert len(ids) == len(set(ids)) == 5
    assert set(ids) == set(get_args(StructuredResourceId))
    assert {r.id for r in RESOURCES if r.contract_available} == set(ids)

    for identifier in ids:
        resource = get_structured_resource(identifier)

        assert get_catalogue_entry(identifier) is resource
        assert resolve(resource.query_contract) is StructuredQuery
        assert resolve(resource.result_contract) is StructuredResult
        assert resource.contract_version == "1.0.0"
        assert resource.execution_available == (
            identifier
            in {
                "analysis.pass_connections",
                "analysis.defensive_map",
            }
        )


@pytest.mark.parametrize(
    "identifier",
    ["passes.attempted", "plot.shots", "not.real"],
)
def test_unknown_or_uncovered_resource_rejected(identifier):
    with pytest.raises(ValueError, match="not registered"):
        get_structured_resource(identifier)


def test_contract_registration_preserves_native_implementation_and_schema():
    resource = get_structured_resource("analysis.pass_connections")

    assert resource.implementation.endswith("pass_network.build_pass_connections")
    assert resource.output_schema.endswith("pass_network.PassConnection")
    assert resource.result_contract.endswith("structured_contracts.StructuredResult")
    assert get_structured_contract(resource.id).team_scope == "required"


def test_partial_contract_metadata_is_rejected():
    native = next(r for r in RESOURCES if not r.contract_available)

    with pytest.raises(ValidationError, match="supplied together"):
        AnalysisResource.model_validate(native.model_dump() | {"query_contract": "module.Query"})


@pytest.mark.parametrize("definition", STRUCTURED_CONTRACTS)
def test_declared_filters_match_query_validation(definition):
    request = {
        "resource_id": definition.resource_id,
        "match_id": 123,
    }
    if definition.team_scope == "required":
        request["team_id"] = "10"

    assert StructuredQuery.model_validate(request)

    for field, value, allowed in (
        ("player_id", "101", definition.player_filter),
        ("periods", (1,), definition.period_filter),
        ("map_options", {}, definition.map_options),
    ):
        candidate = request | {field: value}

        if allowed:
            assert StructuredQuery.model_validate(candidate)
        else:
            with pytest.raises(ValidationError):
                StructuredQuery.model_validate(candidate)

    wrong_team = request | {"team_id": None if definition.team_scope == "required" else "10"}
    with pytest.raises(ValidationError):
        StructuredQuery.model_validate(wrong_team)
