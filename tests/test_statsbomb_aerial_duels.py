import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.aerial_duels import (
    normalize_aerial_duels,
)


def source_event(
    index: int = 1,
    *,
    event_type: str = "Pass",
    aerial_won: object = True,
    include_flag: bool = True,
) -> dict[str, object]:
    event = {
        "id": f"event-{index}",
        "index": index,
        "period": 1,
        "timestamp": "00:02:00.000",
        "type": {"name": event_type},
        "team": {"id": 779, "name": "Argentina"},
        "player": {"id": 5503, "name": "Lionel Messi"},
    }

    if event_type == "Duel":
        event["duel"] = {"type": {"name": "Aerial Lost"}}
    else:
        field = {
            "Pass": "pass",
            "Clearance": "clearance",
            "Shot": "shot",
            "Miscontrol": "miscontrol",
        }[event_type]
        event[field] = {"aerial_won": aerial_won} if include_flag else {}

    return event


@pytest.mark.parametrize("event_type", ["Pass", "Clearance", "Shot", "Miscontrol"])
def test_normalizes_explicit_aerial_wins(event_type: str) -> None:
    result = normalize_aerial_duels(
        [source_event(event_type=event_type)],
        match_id=3869685,
    )

    assert len(result) == 1
    assert result[0].external_event_id == "event-1"
    assert result[0].event_type == event_type
    assert result[0].outcome == "won"
    assert result[0].evidence == "aerial_won_flag"


@pytest.mark.parametrize("event_type", ["Pass", "Clearance", "Shot", "Miscontrol"])
@pytest.mark.parametrize("include_flag", [False, True])
def test_does_not_infer_losses_from_missing_or_false_flags(
    event_type: str,
    include_flag: bool,
) -> None:
    assert (
        normalize_aerial_duels(
            [
                source_event(
                    event_type=event_type,
                    aerial_won=False,
                    include_flag=include_flag,
                )
            ],
            match_id=3869685,
        )
        == []
    )


def test_normalizes_aerial_loss_from_duel_subtype() -> None:
    result = normalize_aerial_duels(
        [source_event(event_type="Duel")],
        match_id=3869685,
    )

    assert result[0].event_type == "Duel"
    assert result[0].outcome == "lost"
    assert result[0].evidence == "aerial_lost_subtype"


def test_combines_results_in_source_order_and_ignores_tackles() -> None:
    tackle = source_event(2, event_type="Duel")
    tackle["duel"] = {
        "type": {"name": "Tackle"},
        "outcome": {"name": "Won"},
    }

    result = normalize_aerial_duels(
        [
            source_event(3),
            tackle,
            source_event(1, event_type="Duel"),
        ],
        match_id=3869685,
    )

    assert [event.external_event_id for event in result] == ["event-1", "event-3"]
    assert [event.outcome for event in result] == ["lost", "won"]


def test_preserves_header_and_shootout_period() -> None:
    event = source_event()
    event["period"] = 5

    result = normalize_aerial_duels([event], match_id=3869685)[0]

    assert result.provider == "statsbomb"
    assert result.external_match_id == "3869685"
    assert result.external_team_id == "779"
    assert result.external_player_id == "5503"
    assert result.source_index == 1
    assert result.timestamp_ms == 120_000
    assert result.period == 5


@pytest.mark.parametrize("flag", [None, "true", 1, {}])
def test_rejects_invalid_or_null_flags(flag: object) -> None:
    with pytest.raises(ProviderUnavailable, match="Invalid aerial details"):
        normalize_aerial_duels(
            [source_event(aerial_won=flag)],
            match_id=3869685,
        )


def test_rejects_contradictory_aerial_loss_outcome() -> None:
    event = source_event(event_type="Duel")
    event["duel"] = {
        "type": {"name": "Aerial Lost"},
        "outcome": {"name": "Won"},
    }

    with pytest.raises(ProviderUnavailable, match="Contradictory"):
        normalize_aerial_duels([event], match_id=3869685)


def test_rejects_duplicate_events() -> None:
    event = source_event()

    with pytest.raises(ProviderUnavailable, match="Repeated"):
        normalize_aerial_duels([event, event], match_id=3869685)


def test_accepts_empty_payload() -> None:
    assert normalize_aerial_duels([], match_id=3869685) == []
