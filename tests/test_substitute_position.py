import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.event_context import (
    normalize_event_context,
)
from football_analytics.integrations.statsbomb.participation_events import (
    normalize_participation_events,
)


def source_event():
    return {
        "id": "event-1",
        "index": 1,
        "period": 2,
        "timestamp": "00:10:00.000",
        "type": {"name": "Bad Behaviour"},
        "team": {"id": 779},
        "player": {"id": 5503},
        "position": {"id": 0, "name": "Substitute"},
        "bad_behaviour": {"card": {"id": 7, "name": "Yellow Card"}},
    }


def test_preserves_substitute_marker_and_source_header() -> None:
    result = normalize_event_context(
        [source_event()],
        match_id=3869685,
    )[0]

    assert result.position is not None
    assert result.position.external_position_id == "0"
    assert result.position.position_name == "Substitute"
    assert result.external_event_id == "event-1"
    assert result.event_type == "Bad Behaviour"
    assert result.timestamp_ms == 600000


def test_preserves_positive_position_ids() -> None:
    source = source_event()
    source["position"] = {"id": 1, "name": "Goalkeeper"}

    result = normalize_event_context(
        [source],
        match_id=3869685,
    )[0]

    assert result.position is not None
    assert result.position.external_position_id == "1"
    assert result.position.position_name == "Goalkeeper"


def test_missing_position_remains_unknown() -> None:
    source = source_event()
    del source["position"]

    result = normalize_event_context(
        [source],
        match_id=3869685,
    )[0]

    assert result.position is None


@pytest.mark.parametrize("name", ["Goalkeeper", "Unknown"])
def test_zero_id_does_not_accept_arbitrary_position_names(name) -> None:
    source = source_event()
    source["position"] = {"id": 0, "name": name}

    with pytest.raises(ProviderUnavailable):
        normalize_event_context([source], match_id=3869685)


def test_negative_position_id_is_rejected() -> None:
    source = source_event()
    source["position"] = {"id": -1, "name": "Substitute"}

    with pytest.raises(ProviderUnavailable):
        normalize_event_context([source], match_id=3869685)


def test_boolean_position_id_is_rejected() -> None:
    source = source_event()
    source["position"] = {"id": True, "name": "Substitute"}

    with pytest.raises(ProviderUnavailable):
        normalize_event_context([source], match_id=3869685)


def test_substitute_yellow_card_does_not_create_field_participation() -> None:
    assert normalize_participation_events([source_event()]) == []
