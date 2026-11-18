import pytest
from pydantic import ValidationError
from test_structured_executor import execute, source_data

from football_analytics.analytics.capabilities import STRUCTURED_EXECUTOR_RESOURCES
from football_analytics.analytics.catalogue import get_catalogue_entry
from football_analytics.analytics.resources import RESOURCES, AnalysisResource
from football_analytics.analytics.structured_executor import SUPPORTED_RESOURCES


def test_catalogue_and_executor_publish_the_same_validated_resources():
    assert STRUCTURED_EXECUTOR_RESOURCES == {
        "analysis.pass_connections",
        "analysis.defensive_map",
    }
    assert SUPPORTED_RESOURCES == STRUCTURED_EXECUTOR_RESOURCES
    assert {r.id for r in RESOURCES if r.execution_available} == STRUCTURED_EXECUTOR_RESOURCES


def test_execution_cannot_be_published_without_contracts():
    native = next(r for r in RESOURCES if not r.contract_available)

    with pytest.raises(ValidationError, match="Execution requires registered"):
        AnalysisResource.model_validate(native.model_dump() | {"execution_available": True})


def test_published_network_preserves_review_and_contract_version():
    data = source_data()
    data[1][2]["pass"]["recipient"]["id"] = 102

    result = execute(payload=data)
    resource = get_catalogue_entry("analysis.pass_connections")

    assert resource.execution_available
    assert result.status == "review"
    assert "recipient_without_origin" in result.reasons
    assert result.contract_version == resource.contract_version == "1.0.0"
