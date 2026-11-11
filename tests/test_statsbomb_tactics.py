import pytest

from football_analytics.integrations.base import ProviderUnavailable
from football_analytics.integrations.statsbomb.tactics import (
    normalize_tactical_snapshots,
)


def source_tactics(
    event_id: str = "tactics-1",
    index: int = 1,
) -> dict[str, object]:
    return {
        "id": event_id,
        "index": index,
        "period": 1,
        "timestamp": "00:00:00.000",
        "type": {"name": "Starting XI"},
        "team": {"id": 779},
        "tactics": {
            "formation": 433,
            "lineup": [
                {
                    "player": {"id": number},
                    "position": {
                        "id": number,
                        "name": f"Position {number}",
                    },
                    "jersey_number": number,
                }
                for number in range(1, 12)
            ],
        },
    }


def test_normalizes_tactical_snapshot() -> None:
    result = normalize_tactical_snapshots(
        [source_tactics()],
        match_id=3869685,
    )

    assert len(result) == 1
    snapshot = result[0]
    assert snapshot.provider == "statsbomb"
    assert snapshot.external_match_id == "3869685"
    assert snapshot.external_event_id == "tactics-1"
    assert snapshot.external_team_id == "779"
    assert snapshot.external_player_id is None
    assert snapshot.source_index == 1
    assert snapshot.period == 1
    assert snapshot.timestamp_ms == 0
    assert snapshot.event_type == "Starting XI"
    assert snapshot.formation == "433"
    assert len(snapshot.lineup) == 11
    assert snapshot.lineup[0].model_dump() == {
        "external_player_id": "1",
        "external_position_id": "1",
        "position_name": "Position 1",
    }


def test_orders_snapshots_and_preserves_repeated_formation() -> None:
    first = source_tactics()
    second = source_tactics("tactics-2", 3)
    second.update(
        type={"name": "Tactical Shift"},
        period=2,
        timestamp="00:10:00.000",
    )
    substitution = {
        "id": "substitution-1",
        "index": 2,
        "period": 2,
        "timestamp": "00:09:00.000",
        "type": {"name": "Substitution"},
    }

    result = normalize_tactical_snapshots(
        [second, substitution, first],
        match_id=3869685,
    )

    assert [item.external_event_id for item in result] == [
        "tactics-1",
        "tactics-2",
    ]
    assert [item.formation for item in result] == ["433", "433"]
    assert result[1].event_type == "Tactical Shift"
    assert result[1].period == 2
    assert result[1].timestamp_ms == 600_000


@pytest.mark.parametrize(
    "tactics",
    [
        None,
        [],
        {},
        {"formation": "433", "lineup": []},
        {"formation": 0, "lineup": []},
        {"formation": 433, "lineup": []},
        {
            "formation": 433,
            "lineup": [{"player": {"id": 1}}],
        },
        {
            "formation": 433,
            "lineup": [
                {
                    "player": {"id": 1},
                    "position": {"id": 1, "name": ""},
                }
            ],
        },
    ],
)
def test_rejects_invalid_tactical_details(tactics: object) -> None:
    event = source_tactics()
    event["tactics"] = tactics

    with pytest.raises(ProviderUnavailable):
        normalize_tactical_snapshots([event], match_id=3869685)


def test_rejects_duplicate_players() -> None:
    event = source_tactics()
    player = {
        "player": {"id": 1},
        "position": {"id": 1, "name": "Goalkeeper"},
    }
    event["tactics"] = {
        "formation": 433,
        "lineup": [player, player],
    }

    with pytest.raises(ProviderUnavailable, match="Invalid tactical details"):
        normalize_tactical_snapshots([event], match_id=3869685)


def test_rejects_missing_team() -> None:
    event = source_tactics()
    del event["team"]

    with pytest.raises(ProviderUnavailable, match="requires a team"):
        normalize_tactical_snapshots([event], match_id=3869685)


def test_rejects_missing_tactics() -> None:
    event = source_tactics()
    del event["tactics"]

    with pytest.raises(ProviderUnavailable, match="Invalid tactical details"):
        normalize_tactical_snapshots([event], match_id=3869685)


def test_rejects_duplicate_event_ids() -> None:
    payload = [
        source_tactics("tactics-1", 1),
        source_tactics("tactics-1", 2),
    ]

    with pytest.raises(ProviderUnavailable, match="Repeated StatsBomb event ID"):
        normalize_tactical_snapshots(payload, match_id=3869685)


def test_accepts_empty_event_list() -> None:
    assert normalize_tactical_snapshots([], match_id=3869685) == []
