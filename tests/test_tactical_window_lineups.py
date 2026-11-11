import pytest

from football_analytics.ingestion.tactical_window_catalogue import TacticalWindow
from football_analytics.ingestion.tactical_window_lineups import build_window_lineups
from football_analytics.integrations.base import MatchLineup
from football_analytics.integrations.statsbomb.participation_events import PlayerAction


def lineups() -> list[MatchLineup]:
    return [
        MatchLineup(
            provider="statsbomb",
            external_match_id="match-1",
            external_team_id=team_id,
            external_player_id=player_id,
            shirt_number=shirt,
            is_starter=starter,
        )
        for team_id, player_id, shirt, starter in [
            ("team-1", "a", 1, True),
            ("team-1", "b", 2, True),
            ("team-1", "c", 3, False),
            ("team-2", "d", 1, True),
            ("team-2", "e", 2, True),
        ]
    ]


def windows() -> list[TacticalWindow]:
    return [
        TacticalWindow.model_validate(
            {
                "provider": "statsbomb",
                "external_match_id": "match-1",
                "period": 1,
                "number_in_period": number,
                "start_elapsed_ms": start_ms,
                "end_elapsed_ms": end_ms,
                "start_source_index": start_index,
                "end_source_index": end_index,
                "start_reasons": (reason,),
                "start_event_ids": (),
            }
        )
        for number, start_ms, end_ms, start_index, end_index, reason in [
            (1, 0, 5000, 1, 5, "period_start"),
            (2, 5000, 10_000, 5, 11, "substitution"),
        ]
    ]


def action(
    player_id: str,
    direction: str,
    reason: str,
    index: int = 5,
) -> PlayerAction:
    return PlayerAction.model_validate(
        {
            "external_player_id": player_id,
            "external_team_id": "team-1",
            "action": direction,
            "reason": reason,
            "event_index": index,
            "period": 1,
            "timestamp": "00:00:05.000",
        }
    )


def team_one(result):
    return [item for item in result if item.external_team_id == "team-1"]


def test_applies_substitution_at_window_start_and_preserves_shirts() -> None:
    result = team_one(
        build_window_lineups(
            lineups(),
            [
                action("b", "leave", "Substitution"),
                action("c", "enter", "Substitution"),
            ],
            windows(),
        )
    )

    assert [player.external_player_id for player in result[0].players] == ["a", "b"]
    assert [player.external_player_id for player in result[1].players] == ["a", "c"]
    assert [player.shirt_number for player in result[1].players] == [1, 3]
    assert result[1].players[1].is_match_starter is False


def test_dismissal_reduces_lineup_without_inventing_replacement() -> None:
    result = team_one(
        build_window_lineups(
            lineups(),
            [action("b", "leave", "Red Card")],
            windows(),
        )
    )

    assert len(result[0].players) == 2
    assert [player.external_player_id for player in result[1].players] == ["a"]


def test_temporary_absence_does_not_change_nominal_lineup() -> None:
    result = team_one(
        build_window_lineups(
            lineups(),
            [
                action("b", "leave", "Player Off", index=2),
                action("b", "enter", "Player On", index=4),
            ],
            windows(),
        )
    )

    assert all(
        {player.external_player_id for player in item.players} == {"a", "b"} for item in result
    )


def test_requires_known_starter_classification() -> None:
    players = lineups()
    players[0].is_starter = None

    with pytest.raises(ValueError, match="Starter classification"):
        build_window_lineups(players, [], windows())


def test_rejects_dismissal_of_player_outside_nominal_lineup() -> None:
    with pytest.raises(ValueError, match="without nominal presence"):
        build_window_lineups(
            lineups(),
            [action("c", "leave", "Red Card")],
            windows(),
        )


def test_rejects_window_that_hides_a_substitution() -> None:
    data = windows()[0].model_dump()
    data.update(end_source_index=11, end_elapsed_ms=10_000)

    with pytest.raises(ValueError, match="spans a permanent lineup change"):
        build_window_lineups(
            lineups(),
            [
                action("b", "leave", "Substitution"),
                action("c", "enter", "Substitution"),
            ],
            [TacticalWindow.model_validate(data)],
        )


def test_rejects_change_for_unlisted_player() -> None:
    with pytest.raises(ValueError, match="unlisted player"):
        build_window_lineups(
            lineups(),
            [action("unknown", "enter", "Substitution")],
            windows(),
        )


def test_accepts_empty_catalogue() -> None:
    assert build_window_lineups(lineups(), [], []) == []
