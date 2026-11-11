import pytest

from football_analytics.ingestion.action_context import ContextualizedAction
from football_analytics.ingestion.tactical_window_catalogue import TacticalWindow
from football_analytics.ingestion.tactical_window_lineups import (
    WindowRosterPlayer,
    WindowTeamLineup,
)
from football_analytics.ingestion.window_pass_network import (
    prepare_window_pass_networks,
)
from football_analytics.integrations.base import (
    MatchEventContext,
    MatchEventSpatial,
    MatchPass,
)


def window_lineups() -> list[WindowTeamLineup]:
    result = []

    for number, start_index, end_index, start_ms, end_ms in [
        (1, 1, 5, 0, 5000),
        (2, 5, 11, 5000, 10_000),
    ]:
        window = TacticalWindow.model_validate(
            {
                "provider": "statsbomb",
                "external_match_id": "match-1",
                "period": 1,
                "number_in_period": number,
                "start_elapsed_ms": start_ms,
                "end_elapsed_ms": end_ms,
                "start_source_index": start_index,
                "end_source_index": end_index,
                "start_reasons": ("period_start",),
                "start_event_ids": (),
            }
        )

        for team_id, players in [
            (
                "team-1",
                [("a", 1), ("b", 2)] if number == 1 else [("a", 1), ("c", 3)],
            ),
            ("team-2", [("d", 1), ("e", 2)]),
        ]:
            result.append(
                WindowTeamLineup(
                    window=window,
                    external_team_id=team_id,
                    players=tuple(
                        WindowRosterPlayer(
                            external_player_id=player_id,
                            shirt_number=shirt,
                            is_match_starter=player_id != "c",
                        )
                        for player_id, shirt in players
                    ),
                )
            )

    return result


def make_pass(
    index: int,
    sender: str,
    recipient: str,
    outcome: str = "complete",
) -> ContextualizedAction:
    header = {
        "provider": "statsbomb",
        "external_match_id": "match-1",
        "external_event_id": f"pass-{index}",
        "source_index": index,
        "period": 1,
        "timestamp_ms": index * 1000,
        "event_type": "Pass",
        "external_team_id": "team-1",
        "external_player_id": sender,
    }

    return ContextualizedAction(
        action=MatchPass.model_validate(
            header
            | {
                "outcome": outcome,
                "external_recipient_player_id": recipient,
            }
        ),
        context=MatchEventContext.model_validate(header),
        spatial=MatchEventSpatial.model_validate(
            header
            | {
                "coordinate_system": "statsbomb_120_80",
                "start_location": {"x": float(index * 10), "y": 30.0},
            }
        ),
    )


def team_one(result):
    return [item for item in result if item.lineup.external_team_id == "team-1"]


def test_recalculates_nodes_and_connections_for_each_window() -> None:
    result = prepare_window_pass_networks(
        [
            make_pass(2, "a", "b"),
            make_pass(3, "b", "a"),
            make_pass(6, "c", "a"),
            make_pass(7, "a", "c"),
        ],
        window_lineups(),
    )
    first, second = team_one(result)

    assert len(result) == 4
    assert first.status == "complete_positions"
    assert second.status == "complete_positions"
    assert {node.external_player_id for node in first.nodes} == {"a", "b"}
    assert {node.external_player_id for node in second.nodes} == {"a", "c"}
    assert next(node for node in first.nodes if node.external_player_id == "a").mean_x == 20
    assert next(node for node in second.nodes if node.external_player_id == "a").mean_x == 70
    assert first.completed_passes == 2
    assert second.completed_passes == 2
    assert all(
        item.status == "no_passes" for item in result if item.lineup.external_team_id == "team-2"
    )


def test_preserves_connection_when_recipient_has_no_origin_samples() -> None:
    first = team_one(
        prepare_window_pass_networks(
            [make_pass(2, "a", "b")],
            window_lineups(),
        )
    )[0]

    assert len(first.lineup.players) == 2
    assert first.players_without_origins == ("b",)
    assert len(first.connections) == 1
    assert first.drawable_connections == ()
    assert first.completed_passes_without_positions == 1
    assert first.status == "no_positioned_connections"


def test_reports_attempts_without_completed_connections() -> None:
    first = team_one(
        prepare_window_pass_networks(
            [make_pass(2, "a", "b", outcome="incomplete")],
            window_lineups(),
        )
    )[0]

    assert first.pass_attempts == 1
    assert first.completed_passes == 0
    assert first.status == "no_completed_passes"


def test_preserves_zero_duration_window() -> None:
    rows = window_lineups()[:2]
    data = rows[0].window.model_dump()
    data["end_elapsed_ms"] = 0
    zero_window = TacticalWindow.model_validate(data)
    rows = [
        WindowTeamLineup(
            window=zero_window,
            external_team_id=row.external_team_id,
            players=row.players,
        )
        for row in rows
    ]

    result = prepare_window_pass_networks([], rows)

    assert len(result) == 2
    assert all(item.status == "zero_duration" for item in result)


def test_reports_partial_positions_without_removing_roster_player() -> None:
    rows = window_lineups()
    first = rows[0]
    rows[0] = WindowTeamLineup(
        window=first.window,
        external_team_id=first.external_team_id,
        players=(
            *first.players,
            WindowRosterPlayer(
                external_player_id="f",
                shirt_number=4,
                is_match_starter=True,
            ),
        ),
    )

    result = team_one(
        prepare_window_pass_networks(
            [make_pass(2, "a", "b"), make_pass(3, "b", "a")],
            rows,
        )
    )[0]

    assert result.status == "partial_positions"
    assert result.players_without_origins == ("f",)
    assert len(result.drawable_connections) == 2
    assert len(result.lineup.players) == 3


@pytest.mark.parametrize(
    ("sender", "recipient"),
    [("c", "a"), ("a", "c")],
)
def test_rejects_pass_reference_outside_nominal_lineup(
    sender: str,
    recipient: str,
) -> None:
    with pytest.raises(ValueError, match="outside nominal lineup"):
        prepare_window_pass_networks(
            [make_pass(2, sender, recipient)],
            window_lineups(),
        )


def test_rejects_duplicate_team_lineup() -> None:
    rows = window_lineups()

    with pytest.raises(ValueError, match="Duplicate team lineup"):
        prepare_window_pass_networks([], [*rows, rows[0]])


def test_accepts_empty_window_selection() -> None:
    assert prepare_window_pass_networks([], []) == []
