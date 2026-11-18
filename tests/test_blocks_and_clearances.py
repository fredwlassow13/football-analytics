import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import (
    MatchBlock,
    MatchClearance,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.blocks_and_clearances import (
    normalize_blocks_and_clearances,
)


def source_event(
    index: int = 1,
    *,
    event_type: str = "Block",
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


def test_preserves_block_identity_clock_and_attributes() -> None:
    event = source_event()
    event["block"] = {
        "offensive": True,
        "deflection": False,
        "save_block": True,
    }
    event["counterpress"] = False

    result = normalize_blocks_and_clearances([event], match_id=3869685)[0]

    assert isinstance(result, MatchBlock)
    assert result.provider == "statsbomb"
    assert result.external_match_id == "3869685"
    assert result.external_event_id == "event-1"
    assert result.external_team_id == "779"
    assert result.external_player_id == "5503"
    assert result.source_index == 1
    assert result.timestamp_ms == 120_000
    assert result.offensive is True
    assert result.deflection is False
    assert result.save_block is True
    assert result.counterpress is False


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Head", "head"),
        ("Left Foot", "left_foot"),
        ("Right Foot", "right_foot"),
        ("Other", "other"),
    ],
)
def test_preserves_clearance_body_part(
    source: str,
    expected: str,
) -> None:
    event = source_event(event_type="Clearance")
    event["clearance"] = {
        "body_part": {"name": source},
        "aerial_won": True,
    }

    result = normalize_blocks_and_clearances([event], match_id=3869685)[0]

    assert isinstance(result, MatchClearance)
    assert result.body_part == expected
    assert result.aerial_won is True
    assert result.external_event_id == "event-1"
    assert result.head is None


@pytest.mark.parametrize("include_details", [False, True])
def test_absent_block_attributes_remain_unknown(
    include_details: bool,
) -> None:
    event = source_event()
    if include_details:
        event["block"] = {}

    result = normalize_blocks_and_clearances([event], match_id=3869685)[0]

    assert isinstance(result, MatchBlock)
    assert result.offensive is None
    assert result.deflection is None
    assert result.save_block is None
    assert result.counterpress is None


@pytest.mark.parametrize("include_details", [False, True])
def test_absent_clearance_attributes_remain_unknown(
    include_details: bool,
) -> None:
    event = source_event(event_type="Clearance")
    if include_details:
        event["clearance"] = {}

    result = normalize_blocks_and_clearances([event], match_id=3869685)[0]

    assert isinstance(result, MatchClearance)
    assert result.body_part is None
    assert result.aerial_won is None


@pytest.mark.parametrize(
    ("event_type", "field"),
    [
        ("Block", "offensive"),
        ("Block", "deflection"),
        ("Block", "save_block"),
        ("Clearance", "aerial_won"),
        ("Clearance", "head"),
        ("Clearance", "left_foot"),
        ("Clearance", "right_foot"),
        ("Clearance", "other"),
    ],
)
@pytest.mark.parametrize("value", [False, True])
def test_preserves_explicit_flags(
    event_type: str,
    field: str,
    value: bool,
) -> None:
    event = source_event(event_type=event_type)
    event[event_type.lower()] = {field: value}

    result = normalize_blocks_and_clearances([event], match_id=3869685)[0]

    assert getattr(result, field) is value


@pytest.mark.parametrize(
    ("event_type", "field"),
    [
        ("Block", "offensive"),
        ("Block", "deflection"),
        ("Block", "save_block"),
        ("Clearance", "aerial_won"),
        ("Clearance", "head"),
        ("Clearance", "left_foot"),
        ("Clearance", "right_foot"),
        ("Clearance", "other"),
    ],
)
@pytest.mark.parametrize("value", [None, "true", 1, 0])
def test_rejects_invalid_or_null_flags(
    event_type: str,
    field: str,
    value: object,
) -> None:
    event = source_event(event_type=event_type)
    event[event_type.lower()] = {field: value}

    with pytest.raises(ProviderUnavailable, match="event-1"):
        normalize_blocks_and_clearances([event], match_id=3869685)


@pytest.mark.parametrize("value", [None, "true", 1])
def test_rejects_invalid_counterpress(value: object) -> None:
    event = source_event()
    event["counterpress"] = value

    with pytest.raises(ProviderUnavailable, match="event-1"):
        normalize_blocks_and_clearances([event], match_id=3869685)


@pytest.mark.parametrize("event_type", ["Block", "Clearance"])
@pytest.mark.parametrize("details", [None, [], "invalid"])
def test_rejects_malformed_details(
    event_type: str,
    details: object,
) -> None:
    event = source_event(event_type=event_type)
    event[event_type.lower()] = details

    with pytest.raises(ProviderUnavailable):
        normalize_blocks_and_clearances([event], match_id=3869685)


@pytest.mark.parametrize(
    "body_part",
    [None, {}, {"name": "Unknown"}, "Head"],
)
def test_rejects_invalid_body_part(body_part: object) -> None:
    event = source_event(event_type="Clearance")
    event["clearance"] = {"body_part": body_part}

    with pytest.raises(ProviderUnavailable):
        normalize_blocks_and_clearances([event], match_id=3869685)


@pytest.mark.parametrize(
    "details",
    [
        {"head": True, "left_foot": True},
        {"body_part": {"name": "Head"}, "head": False},
        {"body_part": {"name": "Head"}, "right_foot": True},
    ],
)
def test_rejects_conflicting_body_part_evidence(
    details: dict[str, object],
) -> None:
    event = source_event(event_type="Clearance")
    event["clearance"] = details

    with pytest.raises(ProviderUnavailable, match="event-1"):
        normalize_blocks_and_clearances([event], match_id=3869685)


def test_filters_orders_and_preserves_shootout() -> None:
    first = source_event(1)
    second = source_event(2, event_type="Clearance")
    second["period"] = 5
    unrelated = source_event(3, event_type="Pass")

    result = normalize_blocks_and_clearances(
        [unrelated, second, first],
        match_id=3869685,
    )

    assert [event.external_event_id for event in result] == [
        "event-1",
        "event-2",
    ]
    assert result[1].period == 5


@pytest.mark.parametrize("duplicate_field", ["id", "index"])
def test_rejects_duplicate_headers(duplicate_field: str) -> None:
    first = source_event(1)
    second = source_event(2, event_type="Clearance")
    second[duplicate_field] = first[duplicate_field]

    with pytest.raises(ProviderUnavailable, match="Repeated"):
        normalize_blocks_and_clearances(
            [first, second],
            match_id=3869685,
        )


@pytest.mark.parametrize("model", [MatchBlock, MatchClearance])
def test_contract_rejects_unexpected_fields(
    model: type[MatchBlock] | type[MatchClearance],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                "provider": "statsbomb",
                "external_match_id": "3869685",
                "external_event_id": "event-1",
                "source_index": 1,
                "period": 1,
                "timestamp_ms": 0,
                "invented": True,
            }
        )


def test_accepts_empty_payload() -> None:
    assert normalize_blocks_and_clearances([], match_id=3869685) == []


@pytest.mark.parametrize("match_id", [0, -1, True])
def test_rejects_invalid_match_id(match_id: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        normalize_blocks_and_clearances([], match_id=match_id)
