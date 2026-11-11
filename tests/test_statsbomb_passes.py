import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.passes import normalize_passes


def source_pass(
    event_id: str = "pass-1",
    index: int = 1,
) -> dict[str, object]:
    return {
        "id": event_id,
        "index": index,
        "period": 2,
        "timestamp": "00:01:00.000",
        "type": {"id": 30, "name": "Pass"},
        "team": {"id": 779},
        "player": {"id": 5503},
        "pass": {
            "length": 12.5,
            "end_location": [80.0, 40.0],
        },
    }


def test_normalizes_completed_pass_and_preserves_header() -> None:
    result = normalize_passes([source_pass()], match_id=3869685)

    assert len(result) == 1
    assert result[0].model_dump() == {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "pass-1",
        "source_index": 1,
        "period": 2,
        "timestamp_ms": 60_000,
        "event_type": "Pass",
        "external_team_id": "779",
        "external_player_id": "5503",
        "outcome": "complete",
        "external_recipient_player_id": None,
        "pass_type": None,
    }


@pytest.mark.parametrize(
    ("source_outcome", "expected"),
    [
        ("Incomplete", "incomplete"),
        ("Out", "out"),
        ("Pass Offside", "offside"),
        ("Unknown", "unknown"),
    ],
)
def test_translates_source_outcomes(
    source_outcome: str,
    expected: str,
) -> None:
    event = source_pass()
    event["pass"] = {"outcome": {"name": source_outcome}}

    result = normalize_passes([event], match_id=3869685)

    assert result[0].outcome == expected


@pytest.mark.parametrize(
    "details",
    [
        None,
        [],
        "invalid",
        {"outcome": None},
        {"outcome": {}},
        {"outcome": {"name": "Unexpected"}},
        {"outcome": {"name": 1}},
    ],
)
def test_rejects_invalid_pass_details(details: object) -> None:
    event = source_pass()
    event["pass"] = details

    with pytest.raises(ProviderUnavailable):
        normalize_passes([event], match_id=3869685)


def test_rejects_missing_pass_details() -> None:
    event = source_pass()
    del event["pass"]

    with pytest.raises(ProviderUnavailable):
        normalize_passes([event], match_id=3869685)


def test_ignores_other_event_types_and_orders_passes() -> None:
    other_event = {
        "id": "shot-1",
        "index": 2,
        "period": 1,
        "timestamp": "00:00:05.000",
        "type": {"name": "Shot"},
    }
    payload = [
        source_pass("pass-2", 3),
        other_event,
        source_pass("pass-1", 1),
    ]

    result = normalize_passes(payload, match_id=3869685)

    assert [event.external_event_id for event in result] == [
        "pass-1",
        "pass-2",
    ]


def test_rejects_duplicate_event_ids() -> None:
    payload = [
        source_pass("pass-1", 1),
        source_pass("pass-1", 2),
    ]

    with pytest.raises(ProviderUnavailable, match="Repeated StatsBomb event ID"):
        normalize_passes(payload, match_id=3869685)


def test_preserves_unknown_as_missing_completion_information() -> None:
    event = source_pass()
    event["pass"] = {"outcome": {"name": "Unknown"}}

    result = normalize_passes([event], match_id=3869685)

    assert result[0].is_completed is None


def test_accepts_empty_event_list() -> None:
    assert normalize_passes([], match_id=3869685) == []
