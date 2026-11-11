import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.events import normalize_events


def source_event(
    event_id: str = "event-1",
    index: int = 1,
) -> dict[str, object]:
    return {
        "id": event_id,
        "index": index,
        "period": 1,
        "timestamp": "00:00:00.000",
        "type": {"id": 35, "name": "Starting XI"},
    }


def test_normalizes_event_header() -> None:
    event = source_event()
    event.update(
        period=2,
        timestamp="00:18:51.930",
        type={"id": 30, "name": "Pass"},
        team={"id": 779, "name": "Argentina"},
        player={"id": 5503, "name": "Lionel Messi"},
    )

    result = normalize_events([event], match_id=3869685)

    assert len(result) == 1
    assert result[0].model_dump() == {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "event-1",
        "source_index": 1,
        "period": 2,
        "timestamp_ms": 1_131_930,
        "event_type": "Pass",
        "external_team_id": "779",
        "external_player_id": "5503",
    }


def test_preserves_event_without_team_or_player() -> None:
    result = normalize_events([source_event()], match_id=3869685)

    assert result[0].external_team_id is None
    assert result[0].external_player_id is None


def test_preserves_penalty_shootout_event() -> None:
    event = source_event()
    event["period"] = 5

    result = normalize_events([event], match_id=3869685)

    assert result[0].period == 5


def test_orders_events_by_source_index() -> None:
    payload = [
        source_event("event-2", 2),
        source_event("event-1", 1),
    ]

    result = normalize_events(payload, match_id=3869685)

    assert [event.external_event_id for event in result] == [
        "event-1",
        "event-2",
    ]


@pytest.mark.parametrize(
    ("second_id", "second_index", "message"),
    [
        ("event-1", 2, "Repeated StatsBomb event ID"),
        ("event-2", 1, "Repeated StatsBomb event index"),
    ],
)
def test_rejects_duplicate_identity_or_order(
    second_id: str,
    second_index: int,
    message: str,
) -> None:
    payload = [
        source_event(),
        source_event(second_id, second_index),
    ]

    with pytest.raises(ProviderUnavailable, match=message):
        normalize_events(payload, match_id=3869685)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", ""),
        ("index", 0),
        ("period", 6),
        ("timestamp", "invalid"),
        ("timestamp", "00:00:60.000"),
        ("team", {"id": 0}),
        ("player", {"id": "5503"}),
        ("type", {"name": ""}),
    ],
)
def test_rejects_invalid_source_fields(field: str, value: object) -> None:
    event = source_event()
    event[field] = value

    with pytest.raises(ProviderUnavailable):
        normalize_events([event], match_id=3869685)


def test_accepts_empty_event_list() -> None:
    assert normalize_events([], match_id=3869685) == []


@pytest.mark.parametrize("match_id", [0, -1, True])
def test_rejects_invalid_match_id(match_id: int) -> None:
    with pytest.raises(ValueError, match="Match ID must be positive"):
        normalize_events([], match_id=match_id)


def test_accepts_additional_source_fields() -> None:
    event = source_event()
    event["tactics"] = {"formation": 433}

    result = normalize_events([event], match_id=3869685)

    assert len(result) == 1
    assert result[0].event_type == "Starting XI"
