import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import MatchPass


def pass_data() -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "synthetic-pass-1",
        "source_index": 10,
        "period": 1,
        "timestamp_ms": 15_000,
        "external_team_id": "779",
        "external_player_id": "5503",
        "outcome": "complete",
    }


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("complete", True),
        ("incomplete", False),
        ("out", False),
        ("offside", False),
        ("unknown", None),
    ],
)
def test_classifies_pass_completion(
    outcome: str,
    expected: bool | None,
) -> None:
    data = pass_data()
    data["outcome"] = outcome

    result = MatchPass.model_validate(data)

    assert result.is_completed is expected


def test_preserves_event_identity_and_time() -> None:
    result = MatchPass.model_validate(pass_data())

    assert result.event_type == "Pass"
    assert result.external_match_id == "3869685"
    assert result.external_event_id == "synthetic-pass-1"
    assert result.external_team_id == "779"
    assert result.external_player_id == "5503"
    assert result.source_index == 10
    assert result.period == 1
    assert result.timestamp_ms == 15_000


@pytest.mark.parametrize("outcome", ["unexpected", "", None, True])
def test_rejects_invalid_outcome(outcome: object) -> None:
    data = pass_data()
    data["outcome"] = outcome

    with pytest.raises(ValidationError):
        MatchPass.model_validate(data)


def test_requires_explicit_normalized_outcome() -> None:
    data = pass_data()
    del data["outcome"]

    with pytest.raises(ValidationError):
        MatchPass.model_validate(data)


def test_rejects_other_event_type() -> None:
    data = pass_data()
    data["event_type"] = "Shot"

    with pytest.raises(ValidationError):
        MatchPass.model_validate(data)
