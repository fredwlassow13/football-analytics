import pytest

from football_analytics.ingestion.player_goalkeeper_statistics import (
    PlayerGoalkeeperStatisticsResult,
    aggregate_player_goalkeeper_statistics,
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


def aggregate(
    keepers: list[MatchGoalkeeperAction],
    shots: list[MatchShot],
    *,
    available: tuple[int, ...] = (1, 2),
    periods: tuple[int, ...] | None = None,
) -> PlayerGoalkeeperStatisticsResult:
    relations = [relation(action, action.related_event_ids) for action in keepers] + [
        relation(action, None) for action in shots
    ]

    return aggregate_player_goalkeeper_statistics(
        keepers,
        shots,
        relations,
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
        available_periods=available,
        periods=periods,
    )


def test_counts_linked_shots_and_preserves_non_shot_actions() -> None:
    result = aggregate(
        [
            keeper(2, ("event-1",)),
            keeper(
                4,
                ("event-3",),
                goalkeeper_type="penalty_conceded",
                outcome="no_touch",
            ),
            keeper(
                5,
                goalkeeper_type="keeper_sweeper",
                outcome="claim",
            ),
        ],
        [
            shot(1),
            shot(3, outcome="goal", shot_type="Penalty"),
        ],
    )
    player = result.players[0]

    assert player.counts.pair_count == 2
    assert player.counts.on_target_saves == 1
    assert player.counts.shot_goals_conceded == 1
    assert player.counts.review_pairs == 0
    assert player.penalty_counts.shot_goals_conceded == 1
    assert player.non_penalty_counts.on_target_saves == 1
    assert player.unknown_penalty_counts.pair_count == 0
    assert player.goalkeeper_event_ids == ("event-2", "event-4", "event-5")
    assert player.candidate_shot_event_ids == ("event-1", "event-3")
    assert player.unlinked_goalkeeper_event_ids == ("event-5",)
    assert sum(action.event_count for action in player.actions) == 3
    assert result.unlinked_shot_event_ids == ()
    assert result.review_pairs == ()


def test_preserves_unknown_penalty_status_as_review() -> None:
    result = aggregate(
        [keeper(2, ("event-1",))],
        [shot(1, shot_type=None)],
    )
    player = result.players[0]

    assert player.counts.review_pairs == 1
    assert player.counts.on_target_saves == 0
    assert player.unknown_penalty_counts.review_pairs == 1
    assert len(result.review_pairs) == 1


def test_reports_unlinked_shot_without_assigning_it_to_goalkeeper() -> None:
    result = aggregate([keeper(2)], [shot(1)])

    assert result.players[0].counts.pair_count == 0
    assert result.players[0].candidate_shot_event_ids == ()
    assert result.unlinked_shot_event_ids == ("event-1",)


def test_preserves_source_issues_and_reviews() -> None:
    result = aggregate(
        [keeper(2, ("event-1", "missing"))],
        [shot(1)],
    )

    assert len(result.source_issues) == 1
    assert result.players[0].counts.review_pairs == 1
    assert result.players[0].counts.on_target_saves == 0


def test_separates_players() -> None:
    result = aggregate(
        [
            keeper(2, ("event-1",)),
            keeper(4, ("event-3",), external_player_id="keeper-2"),
        ],
        [shot(1), shot(3)],
    )

    assert len(result.players) == 2
    assert sum(player.counts.on_target_saves for player in result.players) == 2


def test_partitions_match_and_keeps_shootout_separate() -> None:
    keepers = [
        keeper(2, ("event-1",)),
        keeper(4, ("event-3",), period=3),
        keeper(
            6,
            ("event-5",),
            period=5,
            goalkeeper_type="penalty_saved",
        ),
    ]
    shots = [
        shot(1),
        shot(3, period=3),
        shot(5, period=5, shot_type="Penalty"),
    ]
    available = (1, 2, 3, 4, 5)

    full = aggregate(keepers, shots, available=available)
    normal = aggregate(keepers, shots, available=available, periods=(1, 2))
    extra = aggregate(keepers, shots, available=available, periods=(3, 4))
    shootout = aggregate(keepers, shots, available=available, periods=(5,))

    assert full.periods == (1, 2, 3, 4)
    assert full.players[0].counts.pair_count == 2
    assert full.players[0].counts.pair_count == (
        normal.players[0].counts.pair_count + extra.players[0].counts.pair_count
    )
    assert shootout.players[0].counts.pair_count == 1
    assert shootout.players[0].penalty_counts.on_target_saves == 1


@pytest.mark.parametrize("periods", [(), (1, 1), (3,), (5,), (True,)])
def test_rejects_invalid_or_unavailable_periods(
    periods: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match="Requested periods"):
        aggregate([], [], periods=periods)


def test_rejects_mixed_match_and_shootout() -> None:
    with pytest.raises(ValueError, match="separately"):
        aggregate([], [], available=(1, 2, 5), periods=(1, 5))


@pytest.mark.parametrize("available", [(), (1,), (1, 2, 3), (1, 2, 2), (True, 2)])
def test_rejects_invalid_available_periods(
    available: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match="Available periods"):
        aggregate([], [], available=available)


def test_rejects_source_period_outside_available_periods() -> None:
    with pytest.raises(ValueError, match="incompatible"):
        aggregate([keeper(2, period=3)], [])


@pytest.mark.parametrize("field", ["external_team_id", "external_player_id"])
def test_requires_goalkeeper_references(field: str) -> None:
    with pytest.raises(ValueError, match="team and player"):
        aggregate([keeper(2, **{field: None})], [])


def test_does_not_create_players_without_observed_actions() -> None:
    result = aggregate([], [])

    assert result.players == ()
    assert result.goalkeeper_event_ids == ()
    assert result.shot_event_ids == ()


def test_preserves_provenance() -> None:
    result = aggregate([keeper(2)], [])

    assert result.events_sha256 == "a" * 64
    assert result.formula_version == "1.0.0"
    assert result.classification_policy_version == "1.0.0"
    assert result.link_policy_version == "1.0.0"
