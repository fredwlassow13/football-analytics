import pytest

from football_analytics.ingestion.creation_links import CreationAction
from football_analytics.ingestion.creation_statistics import (
    CreationCounts,
    CreationStatisticsResult,
    build_creation_statistics,
)
from football_analytics.integrations.base import (
    MatchEvent,
    MatchPassCreation,
    MatchShotCreation,
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
        "external_team_id": "779",
        "external_player_id": "passer-1" if event_type == "Pass" else "shooter-1",
    }


def pass_action(
    index: int = 1,
    shot_id: str | None = "event-2",
    **changes: object,
) -> MatchPassCreation:
    return MatchPassCreation.model_validate(
        header(index, "Pass")
        | {
            "outcome": "complete",
            "shot_assist": True,
            "external_assisted_shot_event_id": shot_id,
        }
        | changes
    )


def shot_action(
    index: int = 2,
    pass_id: str | None = "event-1",
    **changes: object,
) -> MatchShotCreation:
    return MatchShotCreation.model_validate(
        header(index, "Shot")
        | {
            "outcome": "saved",
            "shot_type": "Open Play",
            "external_key_pass_event_id": pass_id,
        }
        | changes
    )


def build(
    actions: list[CreationAction],
    *,
    available: tuple[int, ...] = (1, 2),
    periods: tuple[int, ...] | None = None,
) -> CreationStatisticsResult:
    catalogue = [
        MatchEvent.model_validate(action.model_dump(include=set(MatchEvent.model_fields)))
        for action in actions
    ]
    return build_creation_statistics(
        actions,
        catalogue,
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
        available_periods=available,
        periods=periods,
    )


def test_counts_verified_flags_and_preserves_unknown_coverage() -> None:
    result = build(
        [
            pass_action(),
            shot_action(),
            pass_action(3, shot_id=None, shot_assist=None),
            shot_action(4, pass_id=None),
        ]
    )

    assert result.counts.pass_count == 2
    assert result.counts.usable_pairs == 1
    assert result.counts.recorded_shot_assists == 1
    assert result.counts.recorded_goal_assists == 0
    assert result.shot_assist_flags.model_dump() == {"true": 1, "false": 0, "unknown": 1}
    assert result.goal_assist_flags.unknown == 2
    assert result.unlinked_pass_event_ids == ("event-3",)
    assert result.unlinked_shot_event_ids == ("event-4",)


def test_deduplicates_pass_with_both_positive_flags() -> None:
    result = build(
        [
            pass_action(goal_assist=True),
            shot_action(outcome="goal"),
        ]
    )

    assert result.counts.recorded_shot_assists == 1
    assert result.counts.recorded_goal_assists == 1
    assert result.counts.passes_with_any_recorded_flag == 1
    assert result.counts.passes_with_both_recorded_flags == 1
    assert result.usable_pass_event_ids == ("event-1",)


def test_goal_without_flag_does_not_create_recorded_assistance() -> None:
    result = build(
        [
            pass_action(shot_assist=None),
            shot_action(outcome="goal"),
        ]
    )

    assert result.counts.usable_pairs == 1
    assert result.counts.recorded_shot_assists == 0
    assert result.counts.recorded_goal_assists == 0
    assert result.counts.usable_pairs_without_positive_flag == 1
    assert result.goal_assist_flags.unknown == 1


def test_review_preserves_raw_positive_flags_without_credit() -> None:
    result = build(
        [
            pass_action(goal_assist=True),
            shot_action(),
        ]
    )

    assert result.counts.candidate_pairs == 1
    assert result.counts.review_pairs == 1
    assert result.counts.usable_pairs == 0
    assert result.counts.recorded_goal_assists == 0
    assert result.goal_assist_flags.true == 1
    assert len(result.review_pairs) == 1


def test_orphan_positive_flag_is_not_credited() -> None:
    result = build([pass_action(shot_id=None)])

    assert result.shot_assist_flags.true == 1
    assert result.counts.recorded_shot_assists == 0
    assert result.flagged_unlinked_pass_event_ids == ("event-1",)


def test_combines_players_without_duplicating_team_counts() -> None:
    result = build(
        [
            pass_action(),
            shot_action(),
            pass_action(
                3,
                shot_id="event-4",
                external_player_id="passer-2",
                shot_assist=None,
                goal_assist=True,
            ),
            shot_action(4, pass_id="event-3", outcome="goal"),
        ]
    )

    assert len(result.players) == 2
    assert len(result.teams) == 1
    assert result.teams[0].counts == result.counts
    assert result.teams[0].external_player_ids == ("passer-1", "passer-2")

    for field in CreationCounts.model_fields:
        assert getattr(result.counts, field) == sum(
            getattr(player.counts, field) for player in result.players
        )


def test_separates_teams() -> None:
    result = build(
        [
            pass_action(),
            shot_action(),
            pass_action(
                3,
                shot_id="event-4",
                external_team_id="771",
                external_player_id="passer-2",
            ),
            shot_action(4, pass_id="event-3", external_team_id="771"),
        ]
    )

    assert len(result.teams) == 2
    assert sum(team.counts.usable_pairs for team in result.teams) == 2


def test_ambiguous_pass_candidates_do_not_receive_credit() -> None:
    result = build(
        [
            pass_action(1, shot_id="event-3"),
            pass_action(2, shot_id="event-3", external_player_id="passer-2"),
            shot_action(3, pass_id="event-1"),
        ]
    )

    assert result.counts.candidate_pairs == 2
    assert result.counts.review_pairs == 2
    assert result.counts.usable_pairs == 0
    assert result.counts.recorded_shot_assists == 0
    assert result.candidate_shot_event_ids == ("event-3",)


def test_periods_partition_counts_and_exclude_shootout() -> None:
    actions: list[CreationAction] = [
        pass_action(),
        shot_action(),
        pass_action(3, shot_id="event-4", period=3),
        shot_action(4, pass_id="event-3", period=3),
        shot_action(5, pass_id=None, period=5, shot_type="Penalty"),
    ]
    available = (1, 2, 3, 4)

    full = build(actions, available=available)
    normal = build(actions, available=available, periods=(1, 2))
    extra = build(actions, available=available, periods=(3, 4))

    for field in CreationCounts.model_fields:
        assert getattr(full.counts, field) == (
            getattr(normal.counts, field) + getattr(extra.counts, field)
        )

    assert full.shot_event_ids == ("event-2", "event-4")
    assert full.excluded_shootout_creation_event_ids == ("event-5",)


def test_preserves_review_touching_shot_scope_from_another_pass_period() -> None:
    result = build(
        [
            pass_action(period=1),
            shot_action(period=2),
        ],
        periods=(2,),
    )

    assert result.counts.candidate_pairs == 0
    assert result.review_pairs == ()
    assert len(result.cross_scope_review_pairs) == 1


@pytest.mark.parametrize("field", ["external_team_id", "external_player_id"])
def test_requires_passer_identity(field: str) -> None:
    with pytest.raises(ValueError, match="passer team and player"):
        build([pass_action(shot_id=None, **{field: None})])


def test_rejects_passer_assigned_to_multiple_teams() -> None:
    with pytest.raises(ValueError, match="multiple teams"):
        build(
            [
                pass_action(1, shot_id=None),
                pass_action(2, shot_id=None, external_team_id="771"),
            ]
        )


def test_rejects_shootout_as_creation_scope() -> None:
    with pytest.raises(ValueError, match="Requested periods"):
        build([], periods=(5,))


def test_does_not_create_creator_rows_for_shot_only_population() -> None:
    result = build([shot_action(pass_id=None)])

    assert result.players == ()
    assert result.teams == ()
    assert result.unlinked_shot_event_ids == ("event-2",)


def test_empty_input_preserves_provenance() -> None:
    result = build([])

    assert result.players == ()
    assert result.teams == ()
    assert result.pass_event_ids == ()
    assert result.events_sha256 == "a" * 64
    assert result.scope_basis == "pass_period"
