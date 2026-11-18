import pytest
from pydantic import ValidationError

from football_analytics.integrations.base import (
    MatchEventRelations,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.event_relations import (
    normalize_event_relations,
)


def source_event(
    index: int = 1,
    *,
    event_type: str = "Shot",
) -> dict[str, object]:
    return {
        "id": f"event-{index}",
        "index": index,
        "period": 1,
        "timestamp": "00:02:00.000",
        "type": {"name": event_type},
        "team": {"id": 779, "name": "Argentina"},
        "player": {"id": 5503, "name": "Player"},
    }


def test_preserves_identity_and_relationship_order() -> None:
    event = source_event()
    event["related_events"] = ["keeper-1", "block-1"]

    result = normalize_event_relations([event], match_id=3869685)[0]

    assert result.provider == "statsbomb"
    assert result.external_match_id == "3869685"
    assert result.external_event_id == "event-1"
    assert result.source_index == 1
    assert result.timestamp_ms == 120_000
    assert result.event_type == "Shot"
    assert result.external_team_id == "779"
    assert result.external_player_id == "5503"
    assert result.related_event_ids == ("keeper-1", "block-1")


def test_distinguishes_missing_and_explicit_empty_relationships() -> None:
    missing = source_event(1)
    empty = source_event(2)
    empty["related_events"] = []

    result = normalize_event_relations([missing, empty], match_id=3869685)

    assert result[0].related_event_ids is None
    assert result[1].related_event_ids == ()


def test_preserves_both_directions_without_creating_extra_links() -> None:
    shot = source_event(1)
    shot["related_events"] = ["event-2"]

    keeper = source_event(2, event_type="Goal Keeper")
    keeper["related_events"] = ["event-1"]

    result = normalize_event_relations([keeper, shot], match_id=3869685)

    assert len(result) == 2
    assert result[0].external_event_id == "event-1"
    assert result[0].related_event_ids == ("event-2",)
    assert result[1].related_event_ids == ("event-1",)


def test_does_not_infer_reciprocal_relationship() -> None:
    shot = source_event(1)
    shot["related_events"] = ["event-2"]
    keeper = source_event(2, event_type="Goal Keeper")

    result = normalize_event_relations([shot, keeper], match_id=3869685)

    assert result[0].related_event_ids == ("event-2",)
    assert result[1].related_event_ids is None


def test_preserves_duplicate_targets_for_later_audit() -> None:
    event = source_event()
    event["related_events"] = ["keeper-1", "keeper-1"]

    result = normalize_event_relations([event], match_id=3869685)[0]

    assert result.related_event_ids == ("keeper-1", "keeper-1")


def test_preserves_unresolved_target_id() -> None:
    event = source_event()
    event["related_events"] = ["missing-target"]

    result = normalize_event_relations([event], match_id=3869685)[0]

    assert result.related_event_ids == ("missing-target",)


def test_preserves_other_event_types_and_shootout() -> None:
    administrative = source_event(1, event_type="Half Start")
    shootout = source_event(2)
    shootout["period"] = 5

    result = normalize_event_relations([shootout, administrative], match_id=3869685)

    assert len(result) == 2
    assert result[0].event_type == "Half Start"
    assert result[1].period == 5


@pytest.mark.parametrize(
    "value",
    [None, "event-2", [1], [True], [""], {}],
)
def test_rejects_invalid_related_event_values(value: object) -> None:
    event = source_event()
    event["related_events"] = value

    with pytest.raises(ProviderUnavailable, match="event-1"):
        normalize_event_relations([event], match_id=3869685)


@pytest.mark.parametrize("field", ["id", "index"])
def test_rejects_duplicate_source_headers(field: str) -> None:
    first = source_event(1)
    second = source_event(2)
    second[field] = first[field]

    with pytest.raises(ProviderUnavailable, match="Repeated"):
        normalize_event_relations([first, second], match_id=3869685)


def test_contract_rejects_empty_target_id() -> None:
    with pytest.raises(ValidationError, match="nonempty"):
        MatchEventRelations(
            provider="statsbomb",
            external_match_id="3869685",
            external_event_id="event-1",
            source_index=1,
            period=1,
            timestamp_ms=0,
            event_type="Shot",
            related_event_ids=("",),
        )


def test_empty_payload() -> None:
    assert normalize_event_relations([], match_id=3869685) == []


@pytest.mark.parametrize("match_id", [0, -1, True])
def test_rejects_invalid_match_id(match_id: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        normalize_event_relations([], match_id=match_id)
