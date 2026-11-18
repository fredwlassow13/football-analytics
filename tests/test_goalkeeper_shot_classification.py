import pytest

from football_analytics.ingestion.goalkeeper_shot_classification import (
    GoalkeeperShotClassificationResult,
    classify_goalkeeper_shots,
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
        "external_player_id": (
            "keeper-player" if event_type == "Goal Keeper" else "shooter-player"
        ),
    }


def relation(
    event: MatchEvent,
    targets: tuple[str, ...] | None,
) -> MatchEventRelations:
    return MatchEventRelations.model_validate(
        event.model_dump(include=set(MatchEvent.model_fields)) | {"related_event_ids": targets}
    )


def classify(
    *,
    keeper_type: str = "shot_saved",
    keeper_outcome: str | None = "success",
    shot_outcome: str = "saved",
    shot_type: str | None = "Open Play",
    period: int = 1,
    keeper_changes: dict[str, object] | None = None,
    linked: bool = True,
    missing_target: bool = False,
) -> GoalkeeperShotClassificationResult:
    targets = (("event-1", "missing") if missing_target else ("event-1",)) if linked else None

    keeper = MatchGoalkeeperAction.model_validate(
        header(2, "Goal Keeper")
        | {
            "period": period,
            "goalkeeper_type": keeper_type,
            "outcome": keeper_outcome,
            "related_event_ids": targets,
        }
        | (keeper_changes or {})
    )
    shot = MatchShot.model_validate(
        header(1, "Shot")
        | {
            "period": period,
            "outcome": shot_outcome,
            "shot_type": shot_type,
        }
    )

    return classify_goalkeeper_shots(
        [keeper],
        [shot],
        [
            relation(keeper, targets),
            relation(shot, ("event-2",) if linked else None),
        ],
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
    )


@pytest.mark.parametrize(
    ("keeper_type", "shot_outcome", "shot_type", "expected"),
    [
        ("goal_conceded", "goal", "Open Play", "goal_conceded"),
        ("penalty_conceded", "goal", "Penalty", "goal_conceded"),
        ("shot_saved", "saved", "Open Play", "on_target_save"),
        ("penalty_saved", "saved", "Penalty", "on_target_save"),
        (
            "shot_saved_to_post",
            "saved_to_post",
            "Open Play",
            "on_target_save",
        ),
        (
            "penalty_saved_to_post",
            "saved_to_post",
            "Penalty",
            "on_target_save",
        ),
        (
            "shot_saved_off_target",
            "saved_off_target",
            "Open Play",
            "off_target_save",
        ),
        ("shot_faced", "blocked", "Open Play", "other_shot"),
        ("shot_faced", "off_target", "Open Play", "other_shot"),
        ("shot_faced", "post", "Open Play", "other_shot"),
        ("shot_faced", "wayward", "Open Play", "other_shot"),
    ],
)
def test_classifies_compatible_pairs(
    keeper_type: str,
    shot_outcome: str,
    shot_type: str,
    expected: str,
) -> None:
    result = classify(
        keeper_type=keeper_type,
        keeper_outcome=None,
        shot_outcome=shot_outcome,
        shot_type=shot_type,
    )

    row = result.classifications[0]
    assert row.category == expected
    assert row.is_penalty is (shot_type == "Penalty")
    assert row.issues == ()


@pytest.mark.parametrize(
    "outcome",
    ["success", "in_play_danger", "in_play_safe", "saved_twice", "touched_out"],
)
def test_save_outcomes_do_not_multiply_classifications(outcome: str) -> None:
    result = classify(keeper_outcome=outcome)

    assert len(result.classifications) == 1
    assert result.classifications[0].category == "on_target_save"
    assert result.classifications[0].goalkeeper_outcome == outcome


def test_missing_keeper_outcome_remains_missing() -> None:
    row = classify(keeper_outcome=None).classifications[0]

    assert row.category == "on_target_save"
    assert row.goalkeeper_outcome is None


@pytest.mark.parametrize(
    ("keeper_type", "shot_outcome", "shot_type"),
    [
        ("shot_faced", "saved", "Open Play"),
        ("shot_saved", "goal", "Open Play"),
        ("save", "saved", "Open Play"),
        ("saved_to_post", "saved_to_post", "Open Play"),
        ("goal_conceded", "goal", "Penalty"),
        ("penalty_saved", "saved", "Open Play"),
    ],
)
def test_reviews_incompatible_action_types(
    keeper_type: str,
    shot_outcome: str,
    shot_type: str,
) -> None:
    row = classify(
        keeper_type=keeper_type,
        keeper_outcome=None,
        shot_outcome=shot_outcome,
        shot_type=shot_type,
    ).classifications[0]

    assert row.category == "review"
    assert "goalkeeper_type_shot_outcome_mismatch" in row.issues


def test_reviews_explicit_contradictory_keeper_outcome() -> None:
    row = classify(keeper_outcome="fail").classifications[0]

    assert row.category == "review"
    assert "goalkeeper_outcome_mismatch" in row.issues


def test_reviews_unexpected_shot_faced_outcome() -> None:
    row = classify(
        keeper_type="shot_faced",
        keeper_outcome="success",
        shot_outcome="off_target",
    ).classifications[0]

    assert row.category == "review"
    assert "unexpected_shot_faced_outcome" in row.issues


@pytest.mark.parametrize(
    ("shot_type", "issue"),
    [
        (None, "missing_shot_type"),
        ("Unknown", "unsupported_shot_type"),
    ],
)
def test_preserves_unknown_penalty_status(
    shot_type: str | None,
    issue: str,
) -> None:
    row = classify(shot_type=shot_type).classifications[0]

    assert row.category == "review"
    assert row.is_penalty is None
    assert issue in row.issues


def test_preserves_shootout_as_penalty() -> None:
    row = classify(
        keeper_type="penalty_saved",
        shot_type="Penalty",
        period=5,
    ).classifications[0]

    assert row.category == "on_target_save"
    assert row.is_penalty is True
    assert row.goalkeeper_period == row.shot_period == 5


def test_reviews_non_penalty_in_shootout() -> None:
    row = classify(period=5).classifications[0]

    assert row.category == "review"
    assert "shootout_non_penalty" in row.issues


@pytest.mark.parametrize(
    ("changes", "issue"),
    [
        ({"period": 2}, "period_mismatch"),
        ({"external_team_id": "779"}, "same_team"),
        ({"external_team_id": None}, "missing_team_reference"),
        ({"external_player_id": None}, "missing_player_reference"),
    ],
)
def test_propagates_structural_link_issues(
    changes: dict[str, object],
    issue: str,
) -> None:
    row = classify(keeper_changes=changes).classifications[0]

    assert row.category == "review"
    assert issue in row.issues


def test_reviews_source_relationship_problem() -> None:
    result = classify(missing_target=True)
    row = result.classifications[0]

    assert row.category == "review"
    assert "source_relationship_issue" in row.issues
    assert len(result.resolution.source_issues) == 1


def test_does_not_classify_unlinked_events() -> None:
    result = classify(linked=False)

    assert result.classifications == ()
    assert result.resolution.unlinked_goalkeeper_event_ids == ("event-2",)
    assert result.resolution.unlinked_shot_event_ids == ("event-1",)


def test_preserves_provenance_and_pair_evidence() -> None:
    result = classify()

    assert result.policy_version == "1.0.0"
    assert result.resolution.events_sha256 == "a" * 64
    assert result.resolution.pairs[0].evidence == ("goalkeeper_to_shot", "shot_to_goalkeeper")
    assert result.classifications[0].external_goalkeeper_event_id == "event-2"
    assert result.classifications[0].external_shot_event_id == "event-1"
