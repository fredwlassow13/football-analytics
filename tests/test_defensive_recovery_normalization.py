import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import (
    MatchBallRecovery,
    MatchInterception,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.ball_recoveries import normalize_ball_recoveries
from football_analytics.integrations.statsbomb.interceptions import normalize_interceptions


def source_event(index: int = 1, *, event_type: str = "Interception") -> dict[str, object]:
    event: dict[str, object] = {
        "id": f"event-{index}",
        "index": index,
        "period": 1,
        "timestamp": "00:02:00.000",
        "type": {"name": event_type},
        "team": {"id": 779, "name": "Argentina"},
        "player": {"id": 5503, "name": "Lionel Messi"},
    }
    if event_type == "Interception":
        event["interception"] = {"outcome": {"id": 4, "name": "Won"}}
    return event


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Lost", "lost"),
        ("Won", "won"),
        ("Lost In Play", "lost_in_play"),
        ("Lost Out", "lost_out"),
        ("Success", "success"),
        ("Success In Play", "success_in_play"),
        ("Success Out", "success_out"),
    ],
)
def test_preserves_each_interception_result(source: str, expected: str) -> None:
    event = source_event()
    event["interception"] = {"outcome": {"name": source}}
    result = normalize_interceptions([event], match_id=3869685)[0]
    assert result.outcome == expected
    assert result.external_event_id == "event-1"
    assert result.source_index == 1
    assert result.timestamp_ms == 120_000
    assert result.external_match_id == "3869685"
    assert result.provider == "statsbomb"
    assert result.external_team_id == "779"
    assert result.external_player_id == "5503"


@pytest.mark.parametrize("details", [None, {}, {"outcome": None}, {"outcome": {"name": "New"}}])
def test_rejects_missing_or_unknown_interception_results(details: object) -> None:
    event = source_event()
    event["interception"] = details
    with pytest.raises(ProviderUnavailable, match="Invalid interception details.*event-1"):
        normalize_interceptions([event], match_id=3869685)


def test_requires_interception_details() -> None:
    event = source_event()
    del event["interception"]
    with pytest.raises(ProviderUnavailable):
        normalize_interceptions([event], match_id=3869685)


@pytest.mark.parametrize("flag", ["recovery_failure", "offensive"])
@pytest.mark.parametrize("value", [False, True])
def test_preserves_explicit_recovery_flags(flag: str, value: bool) -> None:
    event = source_event(event_type="Ball Recovery")
    event["ball_recovery"] = {flag: value}
    result = normalize_ball_recoveries([event], match_id=3869685)[0]
    assert getattr(result, flag) is value
    assert (
        getattr(result, "offensive" if flag == "recovery_failure" else "recovery_failure") is None
    )
    assert result.external_event_id == "event-1"
    assert result.event_type == "Ball Recovery"
    assert result.timestamp_ms == 120_000


@pytest.mark.parametrize("include_object", [False, True])
def test_absent_recovery_flags_remain_unknown(include_object: bool) -> None:
    event = source_event(event_type="Ball Recovery")
    if include_object:
        event["ball_recovery"] = {}
    result = normalize_ball_recoveries([event], match_id=3869685)[0]
    assert result.recovery_failure is None
    assert result.offensive is None


def test_offensive_recovery_and_failure_can_coexist() -> None:
    event = source_event(event_type="Ball Recovery")
    event["ball_recovery"] = {"offensive": True, "recovery_failure": True}
    result = normalize_ball_recoveries([event], match_id=3869685)[0]
    assert result.offensive is True
    assert result.recovery_failure is True


@pytest.mark.parametrize("flag", ["recovery_failure", "offensive"])
@pytest.mark.parametrize("value", [None, "true", 1, 0, {}, []])
def test_rejects_invalid_or_explicit_null_flags(flag: str, value: object) -> None:
    event = source_event(event_type="Ball Recovery")
    event["ball_recovery"] = {flag: value}
    with pytest.raises(ProviderUnavailable, match="Invalid ball recovery details.*event-1"):
        normalize_ball_recoveries([event], match_id=3869685)


@pytest.mark.parametrize("details", [None, [], "invalid", True])
def test_rejects_malformed_recovery_object(details: object) -> None:
    event = source_event(event_type="Ball Recovery")
    event["ball_recovery"] = details
    with pytest.raises(ProviderUnavailable):
        normalize_ball_recoveries([event], match_id=3869685)


@pytest.mark.parametrize("kind", ["interception", "recovery"])
def test_filters_types_orders_sources_and_keeps_shootout(kind: str) -> None:
    first = source_event(1)
    second = source_event(2, event_type="Ball Recovery")
    third = source_event(
        3, event_type="Interception" if kind == "interception" else "Ball Recovery"
    )
    third["period"] = 5
    payload = [third, second, source_event(4, event_type="Pass"), first]
    result = (
        normalize_interceptions(payload, match_id=3869685)
        if kind == "interception"
        else normalize_ball_recoveries(payload, match_id=3869685)
    )
    assert [e.external_event_id for e in result] == [
        "event-1" if kind == "interception" else "event-2",
        "event-3",
    ]
    assert result[-1].period == 5


@pytest.mark.parametrize("kind", ["interception", "recovery"])
@pytest.mark.parametrize("duplicate_field", ["id", "index"])
def test_rejects_duplicate_headers(kind: str, duplicate_field: str) -> None:
    first = source_event()
    second = source_event(2)
    second[duplicate_field] = first[duplicate_field]
    with pytest.raises(ProviderUnavailable, match="Repeated"):
        if kind == "interception":
            normalize_interceptions([first, second], match_id=3869685)
        else:
            normalize_ball_recoveries([first, second], match_id=3869685)


def test_empty_payloads() -> None:
    assert normalize_interceptions([], match_id=3869685) == []
    assert normalize_ball_recoveries([], match_id=3869685) == []


@pytest.mark.parametrize("kind", ["interception", "recovery"])
def test_invalid_match_id(kind: str) -> None:
    with pytest.raises(ValueError, match="positive"):
        if kind == "interception":
            normalize_interceptions([], match_id=0)
        else:
            normalize_ball_recoveries([], match_id=True)


@pytest.mark.parametrize("kind", ["interception", "recovery"])
def test_contract_rejects_wrong_type_and_extra_fields(kind: str) -> None:
    data = {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "event-1",
        "source_index": 1,
        "period": 1,
        "timestamp_ms": 0,
    }
    model = MatchInterception if kind == "interception" else MatchBallRecovery
    if kind == "interception":
        data["outcome"] = "won"
    with pytest.raises(ValidationError):
        model.model_validate(data | {"event_type": "Pass"})
    with pytest.raises(ValidationError):
        model.model_validate(data | {"invented": True})


def test_interception_contract_requires_known_result() -> None:
    data = {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "event-1",
        "source_index": 1,
        "period": 1,
        "timestamp_ms": 0,
    }
    for extra in ({}, {"outcome": None}, {"outcome": "unknown"}):
        with pytest.raises(ValidationError):
            MatchInterception.model_validate(data | extra)
