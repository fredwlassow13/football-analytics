import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.dribbles import normalize_dribbles


def source_dribble(
    event_id: str = "dribble-1",
    index: int = 1,
) -> dict[str, object]:
    return {
        "id": event_id,
        "index": index,
        "period": 2,
        "timestamp": "00:01:00.000",
        "type": {"id": 14, "name": "Dribble"},
        "team": {"id": 779},
        "player": {"id": 5503},
        "dribble": {
            "outcome": {"name": "Complete"},
            "nutmeg": True,
        },
    }


@pytest.mark.parametrize(
    ("source_outcome", "expected"),
    [
        ("Complete", "complete"),
        ("Incomplete", "incomplete"),
    ],
)
def test_translates_source_outcomes(
    source_outcome: str,
    expected: str,
) -> None:
    event = source_dribble()
    event["dribble"] = {"outcome": {"name": source_outcome}}

    result = normalize_dribbles([event], match_id=3869685)

    assert len(result) == 1
    assert result[0].outcome == expected


@pytest.mark.parametrize(
    "details",
    [
        None,
        [],
        "invalid",
        {},
        {"outcome": None},
        {"outcome": {}},
        {"outcome": {"name": "Unknown"}},
        {"outcome": {"name": 1}},
    ],
)
def test_rejects_invalid_dribble_details(details: object) -> None:
    event = source_dribble()
    event["dribble"] = details

    with pytest.raises(ProviderUnavailable):
        normalize_dribbles([event], match_id=3869685)


def test_rejects_missing_dribble_details() -> None:
    event = source_dribble()
    del event["dribble"]

    with pytest.raises(ProviderUnavailable):
        normalize_dribbles([event], match_id=3869685)


def test_ignores_carries_and_orders_dribbles() -> None:
    carry = {
        "id": "carry-1",
        "index": 2,
        "period": 1,
        "timestamp": "00:00:05.000",
        "type": {"name": "Carry"},
    }
    payload = [
        source_dribble("dribble-2", 3),
        carry,
        source_dribble("dribble-1", 1),
    ]

    result = normalize_dribbles(payload, match_id=3869685)

    assert [event.external_event_id for event in result] == [
        "dribble-1",
        "dribble-2",
    ]


def test_preserves_header_and_accepts_extra_details() -> None:
    result = normalize_dribbles([source_dribble()], match_id=3869685)

    assert result[0].model_dump() == {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "dribble-1",
        "source_index": 1,
        "period": 2,
        "timestamp_ms": 60_000,
        "event_type": "Dribble",
        "external_team_id": "779",
        "external_player_id": "5503",
        "outcome": "complete",
    }


def test_accepts_empty_event_list() -> None:
    assert normalize_dribbles([], match_id=3869685) == []


def test_rejects_duplicate_event_ids() -> None:
    payload = [
        source_dribble("dribble-1", 1),
        source_dribble("dribble-1", 2),
    ]

    with pytest.raises(ProviderUnavailable, match="Repeated StatsBomb event ID"):
        normalize_dribbles(payload, match_id=3869685)
