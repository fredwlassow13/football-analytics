import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import (
    MatchBallControlIncident,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.ball_control import (
    normalize_ball_control_incidents,
)


def source_event(
    index: int = 1,
    *,
    event_type: str = "Dispossessed",
    period: int = 1,
) -> dict[str, object]:
    return {
        "id": f"event-{index}",
        "index": index,
        "period": period,
        "timestamp": "00:01:00.000",
        "type": {"name": event_type},
        "team": {"id": 779},
        "player": {"id": 5503},
    }


@pytest.mark.parametrize("event_type", ["Dispossessed", "Miscontrol"])
def test_normalizes_identity_without_inventing_flags(event_type: str) -> None:
    result = normalize_ball_control_incidents(
        [source_event(event_type=event_type)],
        match_id=3869685,
    )

    assert len(result) == 1
    event = result[0]
    assert event.provider == "statsbomb"
    assert event.external_match_id == "3869685"
    assert event.external_event_id == "event-1"
    assert event.source_index == 1
    assert event.period == 1
    assert event.timestamp_ms == 60_000
    assert event.event_type == event_type
    assert event.external_team_id == "779"
    assert event.external_player_id == "5503"
    assert event.under_pressure is None
    assert event.counterpress is None
    assert event.out is None
    assert event.related_event_ids is None


@pytest.mark.parametrize("value", [True, False])
def test_preserves_explicit_boolean_flags(value: bool) -> None:
    event = source_event()
    event.update(
        under_pressure=value,
        counterpress=value,
        out=value,
    )

    normalized = normalize_ball_control_incidents(
        [event],
        match_id=3869685,
    )[0]

    assert normalized.under_pressure is value
    assert normalized.counterpress is value
    assert normalized.out is value


@pytest.mark.parametrize("related", [[], ["duel-1", "pressure-1"]])
def test_preserves_relationships_without_resolving_targets(
    related: list[str],
) -> None:
    event = source_event()
    event["related_events"] = related

    result = normalize_ball_control_incidents([event], match_id=3869685)

    assert result[0].related_event_ids == tuple(related)


@pytest.mark.parametrize(
    "field",
    ["under_pressure", "counterpress", "out", "related_events"],
)
def test_rejects_explicit_null(field: str) -> None:
    event = source_event()
    event[field] = None

    with pytest.raises(ProviderUnavailable):
        normalize_ball_control_incidents([event], match_id=3869685)


@pytest.mark.parametrize("field", ["under_pressure", "counterpress", "out"])
@pytest.mark.parametrize("value", ["true", 1, [], {}])
def test_rejects_non_boolean_flags(field: str, value: object) -> None:
    event = source_event()
    event[field] = value

    with pytest.raises(ProviderUnavailable):
        normalize_ball_control_incidents([event], match_id=3869685)


@pytest.mark.parametrize("related", ["duel-1", [1], [""], [None]])
def test_rejects_invalid_relationships(related: object) -> None:
    event = source_event()
    event["related_events"] = related

    with pytest.raises(ProviderUnavailable):
        normalize_ball_control_incidents([event], match_id=3869685)


def test_sorts_source_and_ignores_other_types() -> None:
    events = [
        source_event(3, event_type="Miscontrol"),
        source_event(2, event_type="Pressure"),
        source_event(1),
    ]

    result = normalize_ball_control_incidents(events, match_id=3869685)

    assert [event.external_event_id for event in result] == ["event-1", "event-3"]


def test_preserves_shootout_period_without_reclassifying_it() -> None:
    result = normalize_ball_control_incidents(
        [source_event(period=5)],
        match_id=3869685,
    )
    assert result[0].period == 5


def test_preserves_missing_actor_references() -> None:
    event = source_event()
    del event["team"]
    del event["player"]

    result = normalize_ball_control_incidents([event], match_id=3869685)

    assert result[0].external_team_id is None
    assert result[0].external_player_id is None


def test_rejects_duplicate_source_events() -> None:
    event = source_event()

    with pytest.raises(ProviderUnavailable):
        normalize_ball_control_incidents([event, event], match_id=3869685)


def test_contract_rejects_unrelated_action_type() -> None:
    normalized = normalize_ball_control_incidents(
        [source_event()],
        match_id=3869685,
    )[0]
    data = normalized.model_dump()
    data["event_type"] = "Pass"

    with pytest.raises(ValidationError):
        MatchBallControlIncident.model_validate(data)
