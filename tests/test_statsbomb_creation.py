import pytest

from football_analytics.integrations.base import (
    MatchPassCreation,
    MatchShotCreation,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.creation import (
    normalize_creation_events,
)


def source_event(
    index: int = 1,
    *,
    event_type: str = "Pass",
) -> dict[str, object]:
    event: dict[str, object] = {
        "id": f"event-{index}",
        "index": index,
        "period": 1,
        "timestamp": "00:02:00.000",
        "type": {"name": event_type},
        "team": {"id": 779, "name": "Argentina"},
        "player": {"id": 5503, "name": "Player"},
    }
    if event_type == "Pass":
        event["pass"] = {}
    elif event_type == "Shot":
        event["shot"] = {"outcome": {"name": "Saved"}}
    return event


def test_preserves_pass_attributes_and_creation_evidence() -> None:
    event = source_event()
    event["pass"] = {
        "recipient": {"id": 5504, "name": "Recipient"},
        "shot_assist": True,
        "assisted_shot_id": "event-2",
    }

    result = normalize_creation_events([event], match_id=3869685)[0]

    assert isinstance(result, MatchPassCreation)
    assert result.outcome == "complete"
    assert result.external_recipient_player_id == "5504"
    assert result.shot_assist is True
    assert result.goal_assist is None
    assert result.external_assisted_shot_event_id == "event-2"
    assert result.external_event_id == "event-1"
    assert result.source_index == 1
    assert result.timestamp_ms == 120_000


def test_preserves_shot_attributes_and_key_pass() -> None:
    event = source_event(event_type="Shot")
    event["shot"] = {
        "outcome": {"name": "Saved"},
        "type": {"name": "Open Play"},
        "statsbomb_xg": 0.25,
        "key_pass_id": "event-2",
    }

    result = normalize_creation_events([event], match_id=3869685)[0]

    assert isinstance(result, MatchShotCreation)
    assert result.outcome == "saved"
    assert result.shot_type == "Open Play"
    assert result.statsbomb_xg == 0.25
    assert result.is_on_target is True
    assert result.external_key_pass_event_id == "event-2"


@pytest.mark.parametrize("field", ["shot_assist", "goal_assist"])
@pytest.mark.parametrize("value", [False, True])
def test_preserves_independent_flags(field: str, value: bool) -> None:
    event = source_event()
    event["pass"] = {field: value}

    result = normalize_creation_events([event], match_id=3869685)[0]

    assert isinstance(result, MatchPassCreation)
    assert getattr(result, field) is value
    assert getattr(result, "goal_assist" if field == "shot_assist" else "shot_assist") is None
    assert result.external_assisted_shot_event_id is None


def test_does_not_require_flags_to_be_mutually_exclusive() -> None:
    event = source_event()
    event["pass"] = {"shot_assist": True, "goal_assist": True}

    result = normalize_creation_events([event], match_id=3869685)[0]

    assert isinstance(result, MatchPassCreation)
    assert result.shot_assist is True
    assert result.goal_assist is True


def test_reference_does_not_infer_flags() -> None:
    event = source_event()
    event["pass"] = {"assisted_shot_id": "missing-shot"}

    result = normalize_creation_events([event], match_id=3869685)[0]

    assert isinstance(result, MatchPassCreation)
    assert result.external_assisted_shot_event_id == "missing-shot"
    assert result.shot_assist is None
    assert result.goal_assist is None


def test_generic_relations_do_not_create_specific_creation_links() -> None:
    first = source_event(1)
    first["related_events"] = ["event-2"]
    second = source_event(2, event_type="Shot")
    second["related_events"] = ["event-1"]

    result = normalize_creation_events([first, second], match_id=3869685)

    assert isinstance(result[0], MatchPassCreation)
    assert isinstance(result[1], MatchShotCreation)
    assert result[0].external_assisted_shot_event_id is None
    assert result[1].external_key_pass_event_id is None


def test_preserves_both_specific_references_without_resolving_them() -> None:
    first = source_event(1)
    first["pass"] = {"assisted_shot_id": "event-2"}
    second = source_event(2, event_type="Shot")
    second["shot"] = {
        "outcome": {"name": "Saved"},
        "key_pass_id": "event-1",
    }

    result = normalize_creation_events([second, first], match_id=3869685)

    assert isinstance(result[0], MatchPassCreation)
    assert isinstance(result[1], MatchShotCreation)
    assert result[0].external_assisted_shot_event_id == "event-2"
    assert result[1].external_key_pass_event_id == "event-1"


@pytest.mark.parametrize("field", ["shot_assist", "goal_assist"])
@pytest.mark.parametrize("value", [None, "true", 0, 1])
def test_rejects_invalid_or_null_flags(field: str, value: object) -> None:
    event = source_event()
    event["pass"] = {field: value}

    with pytest.raises(ProviderUnavailable, match="event-1"):
        normalize_creation_events([event], match_id=3869685)


@pytest.mark.parametrize("event_type", ["Pass", "Shot"])
@pytest.mark.parametrize("value", [None, "", 1, False])
def test_rejects_invalid_or_null_specific_ids(
    event_type: str,
    value: object,
) -> None:
    event = source_event(event_type=event_type)
    if event_type == "Pass":
        event["pass"] = {"assisted_shot_id": value}
    else:
        event["shot"] = {
            "outcome": {"name": "Saved"},
            "key_pass_id": value,
        }

    with pytest.raises(ProviderUnavailable, match="event-1"):
        normalize_creation_events([event], match_id=3869685)


def test_preserves_missing_creation_attributes() -> None:
    result = normalize_creation_events(
        [source_event(1), source_event(2, event_type="Shot")],
        match_id=3869685,
    )

    assert isinstance(result[0], MatchPassCreation)
    assert isinstance(result[1], MatchShotCreation)
    assert result[0].shot_assist is None
    assert result[0].goal_assist is None
    assert result[0].external_assisted_shot_event_id is None
    assert result[1].external_key_pass_event_id is None


def test_filters_other_types_orders_and_preserves_shootout() -> None:
    first = source_event(1)
    second = source_event(2, event_type="Shot")
    second["period"] = 5

    result = normalize_creation_events(
        [second, source_event(3, event_type="Pressure"), first],
        match_id=3869685,
    )

    assert [event.external_event_id for event in result] == ["event-1", "event-2"]
    assert result[1].period == 5


@pytest.mark.parametrize("field", ["id", "index"])
def test_rejects_duplicate_source_headers(field: str) -> None:
    first = source_event(1)
    second = source_event(2, event_type="Shot")
    second[field] = first[field]

    with pytest.raises(ProviderUnavailable, match="Repeated"):
        normalize_creation_events([first, second], match_id=3869685)


def test_empty_payload() -> None:
    assert normalize_creation_events([], match_id=3869685) == []


@pytest.mark.parametrize("match_id", [0, -1, True])
def test_rejects_invalid_match_id(match_id: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        normalize_creation_events([], match_id=match_id)
