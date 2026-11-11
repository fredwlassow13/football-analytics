import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import MatchDribble


def dribble_data() -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "synthetic-dribble-1",
        "source_index": 100,
        "period": 1,
        "timestamp_ms": 120_000,
        "external_team_id": "779",
        "external_player_id": "5503",
        "outcome": "complete",
    }


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("complete", True),
        ("incomplete", False),
    ],
)
def test_classifies_dribble_completion(
    outcome: str,
    expected: bool,
) -> None:
    data = dribble_data()
    data["outcome"] = outcome

    result = MatchDribble.model_validate(data)

    assert result.is_completed is expected


def test_preserves_event_identity_and_time() -> None:
    result = MatchDribble.model_validate(dribble_data())

    assert result.event_type == "Dribble"
    assert result.external_match_id == "3869685"
    assert result.external_event_id == "synthetic-dribble-1"
    assert result.external_team_id == "779"
    assert result.external_player_id == "5503"
    assert result.source_index == 100
    assert result.period == 1
    assert result.timestamp_ms == 120_000


@pytest.mark.parametrize("outcome", ["unknown", "", None, True])
def test_rejects_invalid_outcome(outcome: object) -> None:
    data = dribble_data()
    data["outcome"] = outcome

    with pytest.raises(ValidationError):
        MatchDribble.model_validate(data)


def test_requires_explicit_outcome() -> None:
    data = dribble_data()
    del data["outcome"]

    with pytest.raises(ValidationError):
        MatchDribble.model_validate(data)


@pytest.mark.parametrize("event_type", ["Carry", "Pass", "Shot"])
def test_rejects_other_event_types(event_type: str) -> None:
    data = dribble_data()
    data["event_type"] = event_type

    with pytest.raises(ValidationError):
        MatchDribble.model_validate(data)
