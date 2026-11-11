import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import MatchShot


def shot_data() -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "synthetic-shot-1",
        "source_index": 100,
        "period": 1,
        "timestamp_ms": 120_000,
        "external_team_id": "779",
        "external_player_id": "5503",
        "outcome": "goal",
    }


@pytest.mark.parametrize(
    ("outcome", "is_goal", "is_on_target"),
    [
        ("goal", True, True),
        ("saved", False, True),
        ("saved_to_post", False, True),
        ("saved_off_target", False, False),
        ("blocked", False, False),
        ("off_target", False, False),
        ("post", False, False),
        ("wayward", False, False),
    ],
)
def test_classifies_shot_outcomes(
    outcome: str,
    is_goal: bool,
    is_on_target: bool,
) -> None:
    data = shot_data()
    data["outcome"] = outcome

    result = MatchShot.model_validate(data)

    assert result.is_goal is is_goal
    assert result.is_on_target is is_on_target


def test_preserves_event_identity_and_time() -> None:
    result = MatchShot.model_validate(shot_data())

    assert result.event_type == "Shot"
    assert result.external_match_id == "3869685"
    assert result.external_event_id == "synthetic-shot-1"
    assert result.external_team_id == "779"
    assert result.external_player_id == "5503"
    assert result.source_index == 100
    assert result.period == 1
    assert result.timestamp_ms == 120_000


def test_preserves_shootout_period() -> None:
    data = shot_data()
    data["period"] = 5

    result = MatchShot.model_validate(data)

    assert result.period == 5


@pytest.mark.parametrize("outcome", ["unexpected", "", None, True])
def test_rejects_invalid_outcome(outcome: object) -> None:
    data = shot_data()
    data["outcome"] = outcome

    with pytest.raises(ValidationError):
        MatchShot.model_validate(data)


def test_requires_explicit_outcome() -> None:
    data = shot_data()
    del data["outcome"]

    with pytest.raises(ValidationError):
        MatchShot.model_validate(data)


def test_rejects_other_event_type() -> None:
    data = shot_data()
    data["event_type"] = "Pass"

    with pytest.raises(ValidationError):
        MatchShot.model_validate(data)
