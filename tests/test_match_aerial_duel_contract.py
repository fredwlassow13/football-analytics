import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import MatchAerialDuel


def aerial_data() -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "source-event-1",
        "source_index": 100,
        "period": 1,
        "timestamp_ms": 120_000,
        "event_type": "Pass",
        "external_team_id": "779",
        "external_player_id": "5503",
        "outcome": "won",
        "evidence": "aerial_won_flag",
    }


@pytest.mark.parametrize("event_type", ["Pass", "Clearance", "Shot", "Miscontrol"])
def test_accepts_explicit_wins_from_supported_event_types(event_type: str) -> None:
    data = aerial_data()
    data["event_type"] = event_type

    result = MatchAerialDuel.model_validate(data)

    assert result.event_type == event_type
    assert result.outcome == "won"
    assert result.evidence == "aerial_won_flag"


def test_accepts_loss_identified_by_duel_subtype() -> None:
    data = aerial_data()
    data.update(
        event_type="Duel",
        outcome="lost",
        evidence="aerial_lost_subtype",
    )

    result = MatchAerialDuel.model_validate(data)

    assert result.outcome == "lost"
    assert result.event_type == "Duel"


def test_preserves_source_identity_and_clock() -> None:
    result = MatchAerialDuel.model_validate(aerial_data())

    assert result.external_event_id == "source-event-1"
    assert result.provider == "statsbomb"
    assert result.external_match_id == "3869685"
    assert result.external_team_id == "779"
    assert result.external_player_id == "5503"
    assert result.source_index == 100
    assert result.period == 1
    assert result.timestamp_ms == 120_000


@pytest.mark.parametrize(
    ("event_type", "outcome", "evidence"),
    [
        ("Duel", "won", "aerial_won_flag"),
        ("Pass", "lost", "aerial_lost_subtype"),
        ("Pass", "won", "aerial_lost_subtype"),
        ("Duel", "lost", "aerial_won_flag"),
    ],
)
def test_rejects_inconsistent_evidence(
    event_type: str,
    outcome: str,
    evidence: str,
) -> None:
    data = aerial_data()
    data.update(
        event_type=event_type,
        outcome=outcome,
        evidence=evidence,
    )

    with pytest.raises(ValidationError):
        MatchAerialDuel.model_validate(data)


@pytest.mark.parametrize("event_type", ["Carry", "Pressure", "Dribble"])
def test_rejects_unsupported_source_types(event_type: str) -> None:
    data = aerial_data()
    data["event_type"] = event_type

    with pytest.raises(ValidationError):
        MatchAerialDuel.model_validate(data)


@pytest.mark.parametrize("outcome", ["unknown", None, True])
def test_requires_known_aerial_result(outcome: object) -> None:
    data = aerial_data()
    data["outcome"] = outcome

    with pytest.raises(ValidationError):
        MatchAerialDuel.model_validate(data)


def test_requires_explicit_evidence() -> None:
    data = aerial_data()
    del data["evidence"]

    with pytest.raises(ValidationError):
        MatchAerialDuel.model_validate(data)
