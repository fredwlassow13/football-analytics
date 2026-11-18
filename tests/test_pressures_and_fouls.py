import pytest

from football_analytics.integrations.base import (
    MatchDribbledPast,
    MatchFoulCommitted,
    MatchFoulWon,
    MatchPressure,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.pressures_and_fouls import (
    normalize_pressures_and_fouls,
)


def source_event(
    index: int = 1,
    *,
    event_type: str = "Pressure",
) -> dict[str, object]:
    return {
        "id": f"event-{index}",
        "index": index,
        "period": 1,
        "timestamp": "00:02:00.000",
        "type": {"name": event_type},
        "team": {"id": 779, "name": "Argentina"},
        "player": {"id": 5503, "name": "Lionel Messi"},
    }


def test_preserves_pressure_and_source_identity() -> None:
    event = source_event()
    event["duration"] = 1.25
    event["counterpress"] = True
    event["related_events"] = ["pass-1", "dribble-2"]

    result = normalize_pressures_and_fouls([event], match_id=3869685)[0]

    assert isinstance(result, MatchPressure)
    assert result.provider == "statsbomb"
    assert result.external_match_id == "3869685"
    assert result.external_event_id == "event-1"
    assert result.external_team_id == "779"
    assert result.external_player_id == "5503"
    assert result.source_index == 1
    assert result.timestamp_ms == 120_000
    assert result.duration_seconds == 1.25
    assert result.counterpress is True
    assert result.related_event_ids == ("pass-1", "dribble-2")


@pytest.mark.parametrize("value", [0, 0.0, 2.5])
def test_accepts_finite_nonnegative_pressure_duration(
    value: float,
) -> None:
    event = source_event()
    event["duration"] = value

    result = normalize_pressures_and_fouls([event], match_id=3869685)[0]

    assert isinstance(result, MatchPressure)
    assert result.duration_seconds == value


@pytest.mark.parametrize("value", [None, -1, "1.5", True, float("inf"), float("nan")])
def test_rejects_invalid_pressure_duration(value: object) -> None:
    event = source_event()
    event["duration"] = value

    with pytest.raises(ProviderUnavailable, match="event-1"):
        normalize_pressures_and_fouls([event], match_id=3869685)


def test_preserves_dribbled_past_without_inferred_success() -> None:
    event = source_event(event_type="Dribbled Past")
    event["counterpress"] = False

    result = normalize_pressures_and_fouls([event], match_id=3869685)[0]

    assert isinstance(result, MatchDribbledPast)
    assert result.counterpress is False
    assert "outcome" not in result.model_dump()


def test_preserves_foul_committed_details() -> None:
    event = source_event(event_type="Foul Committed")
    event["counterpress"] = True
    event["foul_committed"] = {
        "offensive": False,
        "advantage": True,
        "penalty": False,
        "type": {"name": "Handball"},
        "card": {"name": "Yellow Card"},
    }

    result = normalize_pressures_and_fouls([event], match_id=3869685)[0]

    assert isinstance(result, MatchFoulCommitted)
    assert result.offensive is False
    assert result.advantage is True
    assert result.penalty is False
    assert result.counterpress is True
    assert result.foul_type == "Handball"
    assert result.card == "yellow_card"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Yellow Card", "yellow_card"),
        ("Second Yellow", "second_yellow"),
        ("Red Card", "red_card"),
    ],
)
def test_uses_card_names_instead_of_assuming_numeric_ids(
    source: str,
    expected: str,
) -> None:
    event = source_event(event_type="Foul Committed")
    event["foul_committed"] = {"card": {"name": source}}

    result = normalize_pressures_and_fouls([event], match_id=3869685)[0]

    assert isinstance(result, MatchFoulCommitted)
    assert result.card == expected


def test_preserves_foul_won_details_and_links() -> None:
    event = source_event(event_type="Foul Won")
    event["foul_won"] = {
        "defensive": True,
        "advantage": False,
        "penalty": True,
    }
    event["related_events"] = ["committed-1"]

    result = normalize_pressures_and_fouls([event], match_id=3869685)[0]

    assert isinstance(result, MatchFoulWon)
    assert result.defensive is True
    assert result.advantage is False
    assert result.penalty is True
    assert result.related_event_ids == ("committed-1",)


@pytest.mark.parametrize(
    "event_type",
    ["Pressure", "Dribbled Past", "Foul Committed", "Foul Won"],
)
def test_missing_attributes_remain_unknown(event_type: str) -> None:
    result = normalize_pressures_and_fouls(
        [source_event(event_type=event_type)],
        match_id=3869685,
    )[0]

    for name, value in result.model_dump().items():
        if name in {
            "counterpress",
            "duration_seconds",
            "related_event_ids",
            "offensive",
            "defensive",
            "advantage",
            "penalty",
            "foul_type",
            "card",
        }:
            assert value is None


@pytest.mark.parametrize(
    ("event_type", "field"),
    [
        ("Foul Committed", "offensive"),
        ("Foul Committed", "advantage"),
        ("Foul Committed", "penalty"),
        ("Foul Won", "defensive"),
        ("Foul Won", "advantage"),
        ("Foul Won", "penalty"),
    ],
)
@pytest.mark.parametrize("value", [False, True])
def test_preserves_explicit_foul_flags(
    event_type: str,
    field: str,
    value: bool,
) -> None:
    event = source_event(event_type=event_type)
    key = "foul_committed" if event_type == "Foul Committed" else "foul_won"
    event[key] = {field: value}

    result = normalize_pressures_and_fouls([event], match_id=3869685)[0]

    assert getattr(result, field) is value


@pytest.mark.parametrize(
    ("event_type", "field"),
    [
        ("Foul Committed", "offensive"),
        ("Foul Committed", "advantage"),
        ("Foul Committed", "penalty"),
        ("Foul Won", "defensive"),
        ("Foul Won", "advantage"),
        ("Foul Won", "penalty"),
    ],
)
@pytest.mark.parametrize("value", [None, "true", 1])
def test_rejects_invalid_or_null_foul_flags(
    event_type: str,
    field: str,
    value: object,
) -> None:
    event = source_event(event_type=event_type)
    key = "foul_committed" if event_type == "Foul Committed" else "foul_won"
    event[key] = {field: value}

    with pytest.raises(ProviderUnavailable, match="event-1"):
        normalize_pressures_and_fouls([event], match_id=3869685)


@pytest.mark.parametrize("event_type", ["Pressure", "Dribbled Past", "Foul Committed"])
@pytest.mark.parametrize("value", [None, "true", 1])
def test_rejects_invalid_counterpress(
    event_type: str,
    value: object,
) -> None:
    event = source_event(event_type=event_type)
    event["counterpress"] = value

    with pytest.raises(ProviderUnavailable):
        normalize_pressures_and_fouls([event], match_id=3869685)


@pytest.mark.parametrize(
    "details",
    [
        None,
        [],
        {"type": None},
        {"type": {}},
        {"type": {"name": ""}},
        {"card": None},
        {"card": {"name": "Unknown"}},
    ],
)
def test_rejects_invalid_foul_details(details: object) -> None:
    event = source_event(event_type="Foul Committed")
    event["foul_committed"] = details

    with pytest.raises(ProviderUnavailable):
        normalize_pressures_and_fouls([event], match_id=3869685)


@pytest.mark.parametrize("value", [None, "event-2", [1], [""]])
def test_rejects_invalid_related_event_ids(value: object) -> None:
    event = source_event()
    event["related_events"] = value

    with pytest.raises(ProviderUnavailable):
        normalize_pressures_and_fouls([event], match_id=3869685)


def test_preserves_explicit_empty_related_list() -> None:
    event = source_event()
    event["related_events"] = []

    result = normalize_pressures_and_fouls([event], match_id=3869685)[0]

    assert result.related_event_ids == ()


def test_keeps_both_sides_of_foul_as_separate_source_events() -> None:
    committed = source_event(1, event_type="Foul Committed")
    committed["related_events"] = ["event-2"]
    won = source_event(2, event_type="Foul Won")
    won["related_events"] = ["event-1"]

    result = normalize_pressures_and_fouls([committed, won], match_id=3869685)

    assert len(result) == 2
    assert result[0].related_event_ids == ("event-2",)
    assert result[1].related_event_ids == ("event-1",)


def test_orders_filters_and_preserves_shootout() -> None:
    first = source_event(1)
    second = source_event(2, event_type="Foul Won")
    second["period"] = 5

    result = normalize_pressures_and_fouls(
        [second, source_event(3, event_type="Pass"), first],
        match_id=3869685,
    )

    assert [event.external_event_id for event in result] == ["event-1", "event-2"]
    assert result[1].period == 5


@pytest.mark.parametrize("field", ["id", "index"])
def test_rejects_duplicate_headers(field: str) -> None:
    first = source_event(1)
    second = source_event(2)
    second[field] = first[field]

    with pytest.raises(ProviderUnavailable, match="Repeated"):
        normalize_pressures_and_fouls([first, second], match_id=3869685)


def test_empty_payload() -> None:
    assert normalize_pressures_and_fouls([], match_id=3869685) == []


@pytest.mark.parametrize("match_id", [0, -1, True])
def test_rejects_invalid_match_id(match_id: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        normalize_pressures_and_fouls([], match_id=match_id)
