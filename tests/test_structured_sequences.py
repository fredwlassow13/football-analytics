import pytest
from pydantic import ValidationError
from test_structured_executor import execute, source_data

from football_analytics.analytics.structured_contracts import (
    AttackData,
    PossessionData,
    StructuredQuery,
)
from football_analytics.integrations.base import ProviderUnavailable


def query(resource="analysis.possession_fragments", **changes):
    return StructuredQuery(
        **(
            dict(
                resource_id=resource,
                match_id=123,
            )
            | changes
        )
    )


@pytest.mark.parametrize(
    "resource",
    ["analysis.possession_fragments", "analysis.attacks"],
)
def test_full_match_sequences_preserve_source_and_review(resource):
    payload = source_data()
    result = execute(query(resource), payload)

    assert result.status == "review"
    assert "possession_source_issues" in result.reasons
    assert result.source == payload[0]
    assert result.data.value.events_sha256 == result.source.events_sha256
    assert result.data.value.available_periods == result.source.available_periods
    assert result.data.value.issues
    assert result.data.value.external_event_ids

    if resource == "analysis.attacks":
        assert isinstance(result.data, AttackData)
        assert result.data.value.possession_duration_ms is None
        assert result.data.value.possession_percentage is None
    else:
        assert isinstance(result.data, PossessionData)


@pytest.mark.parametrize(
    "resource",
    ["analysis.possession_fragments", "analysis.attacks"],
)
@pytest.mark.parametrize(
    "changes",
    [
        {"periods": (1,)},
        {"team_id": "1"},
        {"player_id": "101"},
    ],
)
def test_partial_context_is_rejected(resource, changes):
    with pytest.raises(ValidationError):
        query(resource, **changes)


def test_fragments_do_not_require_attack_specific_pass_metadata():
    payload = source_data()
    payload[1][2]["pass"] = {
        "outcome": {"name": "Unsupported"},
    }

    result = execute(query(), payload)

    assert isinstance(result.data, PossessionData)
    assert result.status == "review"


def test_attack_normalization_errors_are_not_hidden_as_empty_results():
    payload = source_data()
    payload[1][2]["pass"] = {
        "outcome": {"name": "Unsupported"},
    }

    with pytest.raises(
        ProviderUnavailable,
        match="Invalid pass details",
    ):
        execute(query("analysis.attacks"), payload)
