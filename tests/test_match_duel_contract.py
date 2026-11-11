import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import MatchDuel


def duel_data() -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "duel-1",
        "source_index": 100,
        "period": 1,
        "timestamp_ms": 120_000,
        "external_team_id": "779",
        "external_player_id": "5503",
        "duel_type": "tackle",
        "outcome": "won",
    }


@pytest.mark.parametrize(
    "outcome",
    [
        "lost",
        "won",
        "lost_in_play",
        "lost_out",
        "success",
        "success_in_play",
        "success_out",
    ],
)
def test_preserves_tackle_outcomes(outcome: str) -> None:
    data = duel_data()
    data["outcome"] = outcome

    result = MatchDuel.model_validate(data)

    assert result.duel_type == "tackle"
    assert result.outcome == outcome


def test_preserves_identity_and_clock() -> None:
    result = MatchDuel.model_validate(duel_data())

    assert result.provider == "statsbomb"
    assert result.external_match_id == "3869685"
    assert result.external_event_id == "duel-1"
    assert result.external_team_id == "779"
    assert result.external_player_id == "5503"
    assert result.event_type == "Duel"
    assert result.source_index == 100
    assert result.period == 1
    assert result.timestamp_ms == 120_000


def test_preserves_absent_aerial_lost_outcome() -> None:
    data = duel_data()
    data["duel_type"] = "aerial_lost"
    del data["outcome"]

    result = MatchDuel.model_validate(data)

    assert result.duel_type == "aerial_lost"
    assert result.outcome is None


@pytest.mark.parametrize("explicit_null", [False, True])
def test_requires_tackle_outcome(explicit_null: bool) -> None:
    data = duel_data()
    if explicit_null:
        data["outcome"] = None
    else:
        del data["outcome"]

    with pytest.raises(ValidationError, match="explicit outcome"):
        MatchDuel.model_validate(data)


@pytest.mark.parametrize("outcome", ["unknown", "", True, 1])
def test_rejects_unknown_outcomes(outcome: object) -> None:
    data = duel_data()
    data["outcome"] = outcome

    with pytest.raises(ValidationError):
        MatchDuel.model_validate(data)


@pytest.mark.parametrize("duel_type", ["aerial_won", "", None])
def test_rejects_unsupported_duel_types(duel_type: object) -> None:
    data = duel_data()
    data["duel_type"] = duel_type

    with pytest.raises(ValidationError):
        MatchDuel.model_validate(data)


def test_rejects_unexpected_fields() -> None:
    data = duel_data()
    data["invented_field"] = True

    with pytest.raises(ValidationError):
        MatchDuel.model_validate(data)
