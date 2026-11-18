import pytest

from football_analytics.ingestion.goalkeeper_statistics import (
    GoalkeeperStatisticsResult,
    build_goalkeeper_statistics,
)
from football_analytics.ingestion.player_goalkeeper_statistics import (
    GoalkeeperShotCounts,
)
from football_analytics.integrations.base import (
    MatchEvent,
    MatchEventRelations,
    MatchGoalkeeperAction,
    MatchShot,
)


def header(index: int, event_type: str) -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_event_id": f"event-{index}",
        "source_index": index,
        "period": 1,
        "timestamp_ms": index * 1000,
        "event_type": event_type,
        "external_team_id": "771" if event_type == "Goal Keeper" else "779",
        "external_player_id": ("keeper-1" if event_type == "Goal Keeper" else "shooter-1"),
    }


def keeper(
    index: int,
    targets: tuple[str, ...] | None = None,
    **changes: object,
) -> MatchGoalkeeperAction:
    return MatchGoalkeeperAction.model_validate(
        header(index, "Goal Keeper")
        | {
            "goalkeeper_type": "shot_saved",
            "outcome": "success",
            "related_event_ids": targets,
        }
        | changes
    )


def shot(index: int, **changes: object) -> MatchShot:
    return MatchShot.model_validate(
        header(index, "Shot") | {"outcome": "saved", "shot_type": "Open Play"} | changes
    )


def relation(
    event: MatchEvent,
    targets: tuple[str, ...] | None,
) -> MatchEventRelations:
    return MatchEventRelations.model_validate(
        event.model_dump(include=set(MatchEvent.model_fields)) | {"related_event_ids": targets}
    )


def build(
    keepers: list[MatchGoalkeeperAction],
    shots: list[MatchShot],
    *,
    available: tuple[int, ...] = (1, 2),
    periods: tuple[int, ...] | None = None,
) -> GoalkeeperStatisticsResult:
    relations = [relation(action, action.related_event_ids) for action in keepers] + [
        relation(action, None) for action in shots
    ]

    return build_goalkeeper_statistics(
        keepers,
        shots,
        relations,
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
        available_periods=available,
        periods=periods,
    )


def test_combines_goalkeepers_and_reconciles_count_partitions() -> None:
    result = build(
        [
            keeper(2, ("event-1",)),
            keeper(
                4,
                ("event-3",),
                external_player_id="keeper-2",
                goalkeeper_type="penalty_conceded",
                outcome="no_touch",
            ),
            keeper(
                5,
                goalkeeper_type="punch",
                outcome="in_play_safe",
            ),
        ],
        [
            shot(1),
            shot(3, outcome="goal", shot_type="Penalty"),
        ],
    )
    team = result.teams[0]

    assert team.external_player_ids == ("keeper-1", "keeper-2")
    assert team.counts.pair_count == 2
    assert team.counts.on_target_saves == 1
    assert team.counts.shot_goals_conceded == 1
    assert team.goalkeeper_event_ids == ("event-2", "event-4", "event-5")
    assert team.candidate_shot_event_ids == ("event-1", "event-3")
    assert team.unlinked_goalkeeper_event_ids == ("event-5",)
    assert sum(action.event_count for action in team.actions) == 3

    for field in GoalkeeperShotCounts.model_fields:
        assert getattr(team.counts, field) == sum(
            getattr(player.counts, field) for player in result.players
        )
        assert getattr(team.counts, field) == (
            getattr(team.penalty_counts, field)
            + getattr(team.non_penalty_counts, field)
            + getattr(team.unknown_penalty_counts, field)
        )


def test_separates_teams() -> None:
    result = build(
        [
            keeper(2, ("event-1",)),
            keeper(
                4,
                ("event-3",),
                external_player_id="keeper-2",
                external_team_id="779",
            ),
        ],
        [
            shot(1),
            shot(3, external_team_id="771"),
        ],
    )

    assert len(result.teams) == 2
    assert {team.external_team_id for team in result.teams} == {"771", "779"}
    assert sum(team.counts.on_target_saves for team in result.teams) == 2


def test_deduplicates_candidate_shots_but_preserves_review_pairs() -> None:
    result = build(
        [
            keeper(2, ("event-1",)),
            keeper(3, ("event-1",), external_player_id="keeper-2"),
        ],
        [shot(1)],
    )
    team = result.teams[0]

    assert team.counts.pair_count == 2
    assert team.counts.review_pairs == 2
    assert team.counts.on_target_saves == 0
    assert team.candidate_shot_event_ids == ("event-1",)
    assert len(result.review_pairs) == 2


def test_merges_action_breakdown_in_source_order() -> None:
    result = build(
        [
            keeper(
                4,
                external_player_id="keeper-2",
                goalkeeper_type="keeper_sweeper",
                outcome="claim",
            ),
            keeper(2, goalkeeper_type="keeper_sweeper", outcome="claim"),
        ],
        [],
    )

    action = result.teams[0].actions[0]
    assert action.goalkeeper_type == "keeper_sweeper"
    assert action.outcome == "claim"
    assert action.event_count == 2
    assert action.external_event_ids == ("event-2", "event-4")
    assert result.teams[0].counts.pair_count == 0


def test_selects_shootout_separately() -> None:
    result = build(
        [
            keeper(2, ("event-1",)),
            keeper(
                4,
                ("event-3",),
                period=5,
                goalkeeper_type="penalty_saved",
            ),
        ],
        [
            shot(1),
            shot(3, period=5, shot_type="Penalty"),
        ],
        available=(1, 2, 5),
        periods=(5,),
    )

    assert result.periods == (5,)
    assert result.teams[0].goalkeeper_event_ids == ("event-4",)
    assert result.teams[0].penalty_counts.on_target_saves == 1
    assert result.teams[0].non_penalty_counts.pair_count == 0


def test_reuses_player_team_validation() -> None:
    with pytest.raises(ValueError, match="multiple teams"):
        build(
            [
                keeper(2),
                keeper(4, external_team_id="779"),
            ],
            [],
        )


def test_empty_scope_preserves_metadata_without_creating_teams() -> None:
    result = build([], [])

    assert result.players == ()
    assert result.teams == ()
    assert result.events_sha256 == "a" * 64
    assert result.periods == (1, 2)
    assert result.formula_version == "1.0.0"
