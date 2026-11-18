import pytest
from test_shootout_participation import event

from football_analytics.ingestion.shootout_roster import (
    build_shootout_roster,
)
from football_analytics.integrations.base import MatchEvent, MatchLineup


def lineup(player_id: str, **changes: object) -> MatchLineup:
    return MatchLineup.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "3869685",
            "external_team_id": "779",
            "external_player_id": player_id,
            "is_starter": None,
        }
        | changes
    )


def build(
    events: list[MatchEvent],
    lineups: list[MatchLineup],
):
    return build_shootout_roster(
        events,
        lineups,
        provider="statsbomb",
        match_id="3869685",
    )


def test_includes_all_listed_players_in_stable_order() -> None:
    result = build(
        [event(1)],
        [lineup("player-2"), lineup("player-1")],
    )

    assert [row.lineup.external_player_id for row in result] == [
        "player-1",
        "player-2",
    ]
    assert result[0].participation_status == "observed"
    assert result[0].evidence[0].external_event_id == "event-1"
    assert result[1].participation_status == "unknown"
    assert result[1].evidence == ()


def test_no_shootout_events_preserves_unknown_participation() -> None:
    result = build(
        [event(1, period=4)],
        [lineup("player-1")],
    )

    assert result[0].participation_status == "unknown"
    assert result[0].evidence == ()


def test_preserves_multiple_roles_and_source_order() -> None:
    result = build(
        [
            event(2),
            event(1, event_type="Goal Keeper"),
        ],
        [lineup("player-1")],
    )

    assert [item.role for item in result[0].evidence] == [
        "goalkeeper",
        "shooter",
    ]


def test_rejects_unlisted_participant() -> None:
    with pytest.raises(ValueError, match="unlisted player"):
        build([event(1)], [lineup("player-2")])


def test_rejects_player_team_mismatch() -> None:
    with pytest.raises(ValueError, match="another team"):
        build(
            [event(1, external_team_id="771")],
            [lineup("player-1")],
        )


def test_rejects_duplicate_lineup_player() -> None:
    with pytest.raises(ValueError, match="Duplicate lineup"):
        build([], [lineup("player-1"), lineup("player-1")])


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "other"},
        {"external_match_id": "other"},
    ],
)
def test_rejects_lineup_from_another_source(changes) -> None:
    with pytest.raises(ValueError, match="another source or match"):
        build([], [lineup("player-1", **changes)])


def test_incomplete_evidence_requires_review() -> None:
    with pytest.raises(ValueError, match="requires review"):
        build(
            [event(1, external_player_id=None)],
            [lineup("player-1")],
        )
