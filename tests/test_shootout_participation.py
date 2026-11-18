import pytest

from football_analytics.ingestion.shootout_participation import (
    collect_shootout_participation,
)
from football_analytics.integrations.base import MatchEvent


def event(index: int, **changes: object) -> MatchEvent:
    return MatchEvent.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_event_id": f"event-{index}",
            "source_index": index,
            "period": 5,
            "timestamp_ms": index * 1000,
            "event_type": "Shot",
            "external_team_id": "779",
            "external_player_id": "player-1",
        }
        | changes
    )


def collect(events: list[MatchEvent]):
    return collect_shootout_participation(
        events,
        provider="statsbomb",
        match_id="3869685",
    )


def test_identifies_roles_in_source_order() -> None:
    result = collect(
        [
            event(2, event_type="Goal Keeper", external_player_id="keeper-1"),
            event(1),
            event(3, event_type="Bad Behaviour"),
        ]
    )

    assert [item.external_event_id for item in result] == [
        "event-1",
        "event-2",
    ]
    assert [item.role for item in result] == ["shooter", "goalkeeper"]
    assert all(item.status == "observed" for item in result)


def test_match_penalties_are_not_shootout_participation() -> None:
    assert collect([event(1, period=2), event(2, period=4)]) == ()


def test_missing_identity_requires_review() -> None:
    result = collect([event(1, external_team_id=None, external_player_id=None)])

    assert result[0].status == "review"
    assert result[0].issues == ("missing_team", "missing_player")
    assert result[0].external_player_id is None


def test_repeated_player_keeps_each_source_evidence() -> None:
    result = collect(
        [
            event(1, event_type="Goal Keeper"),
            event(2, event_type="Goal Keeper"),
        ]
    )

    assert len(result) == 2
    assert result[0].external_player_id == result[1].external_player_id


@pytest.mark.parametrize(
    "changes",
    [
        {"external_event_id": "event-1"},
        {"source_index": 1},
    ],
)
def test_rejects_duplicate_sources(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        collect([event(1), event(2, **changes)])


@pytest.mark.parametrize(
    "changes",
    [
        {"external_match_id": "other"},
        {"provider": "other"},
    ],
)
def test_rejects_mixed_sources(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="another source or match"):
        collect([event(1, **changes)])


def test_empty_input_does_not_infer_absence_or_ineligibility() -> None:
    assert collect([]) == ()


def test_rejects_unsupported_period() -> None:
    with pytest.raises(ValueError, match="Unsupported event period"):
        collect([event(1, period=6)])
