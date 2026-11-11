import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.shots import normalize_shots


def source_shot(
    event_id: str = "shot-1",
    index: int = 1,
) -> dict[str, object]:
    return {
        "id": event_id,
        "index": index,
        "period": 2,
        "timestamp": "00:01:00.000",
        "type": {"id": 16, "name": "Shot"},
        "team": {"id": 779},
        "player": {"id": 5503},
        "shot": {
            "outcome": {"id": 97, "name": "Goal"},
            "statsbomb_xg": 0.25,
            "end_location": [120.0, 40.0, 1.0],
        },
    }


@pytest.mark.parametrize(
    ("source_outcome", "expected"),
    [
        ("Goal", "goal"),
        ("Saved", "saved"),
        ("Saved to Post", "saved_to_post"),
        ("Saved To Post", "saved_to_post"),
        ("Saved Off Target", "saved_off_target"),
        ("Blocked", "blocked"),
        ("Off T", "off_target"),
        ("Post", "post"),
        ("Wayward", "wayward"),
    ],
)
def test_translates_source_outcomes(
    source_outcome: str,
    expected: str,
) -> None:
    event = source_shot()
    event["shot"] = {"outcome": {"name": source_outcome}}

    result = normalize_shots([event], match_id=3869685)

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
        {"outcome": {"name": "Unexpected"}},
        {"outcome": {"name": 1}},
    ],
)
def test_rejects_invalid_shot_details(details: object) -> None:
    event = source_shot()
    event["shot"] = details

    with pytest.raises(ProviderUnavailable):
        normalize_shots([event], match_id=3869685)


def test_rejects_missing_shot_details() -> None:
    event = source_shot()
    del event["shot"]

    with pytest.raises(ProviderUnavailable):
        normalize_shots([event], match_id=3869685)


def test_ignores_other_events_and_orders_shots() -> None:
    other_event = {
        "id": "pass-1",
        "index": 2,
        "period": 1,
        "timestamp": "00:00:05.000",
        "type": {"name": "Pass"},
    }
    payload = [
        source_shot("shot-2", 3),
        other_event,
        source_shot("shot-1", 1),
    ]

    result = normalize_shots(payload, match_id=3869685)

    assert [event.external_event_id for event in result] == [
        "shot-1",
        "shot-2",
    ]


def test_preserves_header_and_shootout_period() -> None:
    event = source_shot()
    event["period"] = 5

    result = normalize_shots([event], match_id=3869685)

    assert result[0].model_dump() == {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "shot-1",
        "source_index": 1,
        "period": 5,
        "timestamp_ms": 60_000,
        "event_type": "Shot",
        "external_team_id": "779",
        "external_player_id": "5503",
        "outcome": "goal",
        "body_part": None,
        "technique": None,
        "shot_type": None,
        "statsbomb_xg": 0.25,
    }


def test_accepts_empty_event_list() -> None:
    assert normalize_shots([], match_id=3869685) == []


def test_rejects_duplicate_event_ids() -> None:
    payload = [
        source_shot("shot-1", 1),
        source_shot("shot-1", 2),
    ]

    with pytest.raises(ProviderUnavailable, match="Repeated StatsBomb event ID"):
        normalize_shots(payload, match_id=3869685)
