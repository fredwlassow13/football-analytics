import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.duels import normalize_duels


def source_duel(
    index: int = 1,
    *,
    outcome: str = "Won",
) -> dict[str, object]:
    return {
        "id": f"duel-{index}",
        "index": index,
        "period": 1,
        "timestamp": "00:02:00.000",
        "type": {"name": "Duel"},
        "team": {"id": 779, "name": "Argentina"},
        "player": {"id": 5503, "name": "Lionel Messi"},
        "duel": {
            "type": {"name": "Tackle"},
            "outcome": {"name": outcome},
        },
    }


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
def test_normalizes_tackle_outcomes(source: str, expected: str) -> None:
    result = normalize_duels(
        [source_duel(outcome=source)],
        match_id=3869685,
    )

    assert len(result) == 1
    assert result[0].duel_type == "tackle"
    assert result[0].outcome == expected


@pytest.mark.parametrize("explicit_null", [False, True])
def test_preserves_missing_aerial_lost_outcome(explicit_null: bool) -> None:
    event = source_duel()
    details = {"type": {"name": "Aerial Lost"}}
    if explicit_null:
        event["duel"] = details | {"outcome": None}
    else:
        event["duel"] = details

    result = normalize_duels([event], match_id=3869685)

    assert result[0].duel_type == "aerial_lost"
    assert result[0].outcome is None


def test_preserves_header_and_shootout_period() -> None:
    event = source_duel()
    event["period"] = 5

    result = normalize_duels([event], match_id=3869685)[0]

    assert result.model_dump() == {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": "duel-1",
        "source_index": 1,
        "period": 5,
        "timestamp_ms": 120_000,
        "event_type": "Duel",
        "external_team_id": "779",
        "external_player_id": "5503",
        "duel_type": "tackle",
        "outcome": "won",
    }


def test_orders_duels_and_ignores_other_event_types() -> None:
    other = source_duel(2)
    other["type"] = {"name": "Pass"}
    del other["duel"]

    result = normalize_duels(
        [source_duel(3), other, source_duel(1)],
        match_id=3869685,
    )

    assert [event.external_event_id for event in result] == ["duel-1", "duel-3"]


@pytest.mark.parametrize(
    "details",
    [
        None,
        {},
        {"type": None},
        {"type": {"name": "Unknown"}},
        {"type": {"name": "Tackle"}},
        {"type": {"name": "Tackle"}, "outcome": None},
        {
            "type": {"name": "Tackle"},
            "outcome": {"name": "Unknown"},
        },
    ],
)
def test_rejects_invalid_duel_details(details: object) -> None:
    event = source_duel()
    event["duel"] = details

    with pytest.raises(ProviderUnavailable, match="Invalid duel details"):
        normalize_duels([event], match_id=3869685)


def test_rejects_duplicate_source_events() -> None:
    event = source_duel()

    with pytest.raises(ProviderUnavailable, match="Repeated"):
        normalize_duels([event, event], match_id=3869685)


def test_accepts_empty_payload() -> None:
    assert normalize_duels([], match_id=3869685) == []
