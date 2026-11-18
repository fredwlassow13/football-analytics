import pytest

from football_analytics.ingestion.creation_classification import (
    CreationClassificationResult,
    classify_creation_links,
)
from football_analytics.ingestion.creation_links import CreationAction
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
        "external_player_id": "passer" if event_type == "Pass" else "shooter",
    }


def classify_actions(
    actions: list[CreationAction],
) -> CreationClassificationResult:
    catalogue = [
        MatchEvent.model_validate(action.model_dump(include=set(MatchEvent.model_fields)))
        for action in actions
    ]
    return classify_creation_links(
        actions,
        catalogue,
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
    )


def classify(
    *,
    pass_changes: dict[str, object] | None = None,
    shot_changes: dict[str, object] | None = None,
    linked: bool = True,
) -> CreationClassificationResult:
    pass_action = MatchPassCreation.model_validate(
        header(1, "Pass")
        | {
            "outcome": "complete",
            "shot_assist": True,
            "external_assisted_shot_event_id": "event-2" if linked else None,
        }
        | (pass_changes or {})
    )
    shot_action = MatchShotCreation.model_validate(
        header(2, "Shot")
        | {
            "outcome": "saved",
            "shot_type": "Open Play",
            "external_key_pass_event_id": "event-1" if linked else None,
        }
        | (shot_changes or {})
    )
    return classify_actions([pass_action, shot_action])


def test_preserves_explicit_shot_assist_and_unknown_goal_assist() -> None:
    row = classify().classifications[0]

    assert row.status == "usable"
    assert row.shot_assist is True
    assert row.goal_assist is None
    assert row.issues == ()


def test_preserves_explicit_goal_assist() -> None:
    row = classify(
        pass_changes={"shot_assist": None, "goal_assist": True},
        shot_changes={"outcome": "goal"},
    ).classifications[0]

    assert row.status == "usable"
    assert row.shot_assist is None
    assert row.goal_assist is True


def test_goal_does_not_infer_missing_assistance_flags() -> None:
    row = classify(
        pass_changes={"shot_assist": None},
        shot_changes={"outcome": "goal"},
    ).classifications[0]

    assert row.status == "usable"
    assert row.shot_assist is None
    assert row.goal_assist is None


def test_does_not_force_flags_to_be_mutually_exclusive() -> None:
    row = classify(
        pass_changes={"goal_assist": True},
        shot_changes={"outcome": "goal"},
    ).classifications[0]

    assert row.status == "usable"
    assert row.shot_assist is True
    assert row.goal_assist is True


def test_preserves_explicit_false_flags() -> None:
    row = classify(
        pass_changes={"shot_assist": False, "goal_assist": False},
    ).classifications[0]

    assert row.status == "usable"
    assert row.shot_assist is False
    assert row.goal_assist is False


def test_reviews_goal_assist_without_goal_without_erasing_flag() -> None:
    row = classify(
        pass_changes={"goal_assist": True},
    ).classifications[0]

    assert row.status == "review"
    assert row.goal_assist is True
    assert "goal_assist_without_goal" in row.issues


@pytest.mark.parametrize("outcome", ["incomplete", "unknown", "out", "offside"])
def test_reviews_non_complete_passes(outcome: str) -> None:
    row = classify(
        pass_changes={"outcome": outcome},
    ).classifications[0]

    assert row.status == "review"
    assert row.pass_outcome == outcome
    assert "pass_not_complete" in row.issues


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"period": 2}, "period_mismatch"),
        ({"external_team_id": "771"}, "different_teams"),
        ({"external_player_id": None}, "missing_player_reference"),
    ],
)
def test_propagates_link_diagnostics(
    changes: dict[str, object],
    expected: str,
) -> None:
    row = classify(pass_changes=changes).classifications[0]

    assert row.status == "review"
    assert expected in row.issues


def test_reviews_source_reference_problem_even_with_reverse_link() -> None:
    result = classify(
        pass_changes={"external_assisted_shot_event_id": "missing"},
    )

    assert len(result.classifications) == 1
    assert result.classifications[0].status == "review"
    assert "source_reference_issue" in result.classifications[0].issues
    assert len(result.resolution.reference_issues) == 1


@pytest.mark.parametrize(
    ("shot_type", "expected"),
    [
        (None, "missing_shot_type"),
        ("Unknown", "unsupported_shot_type"),
        ("Penalty", "penalty_creation_not_defined"),
    ],
)
def test_reviews_shot_types_outside_current_policy(
    shot_type: str | None,
    expected: str,
) -> None:
    row = classify(
        shot_changes={"shot_type": shot_type},
    ).classifications[0]

    assert row.status == "review"
    assert expected in row.issues


def test_preserves_shootout_pair_without_crediting_creation() -> None:
    row = classify(
        pass_changes={"period": 5},
        shot_changes={"period": 5, "shot_type": "Penalty"},
    ).classifications[0]

    assert row.status == "review"
    assert "shootout_creation_not_defined" in row.issues


def test_does_not_convert_orphan_positive_flag_into_zero_or_pair() -> None:
    result = classify(linked=False)

    assert result.classifications == ()
    assert result.resolution.flagged_unlinked_pass_event_ids == ("event-1",)
    assert result.resolution.unlinked_shot_event_ids == ("event-2",)


def test_ambiguous_candidates_remain_in_review() -> None:
    pass_action = MatchPassCreation.model_validate(
        header(1, "Pass")
        | {
            "outcome": "complete",
            "shot_assist": True,
            "external_assisted_shot_event_id": "event-2",
        }
    )
    shots = [
        MatchShotCreation.model_validate(
            header(index, "Shot")
            | {
                "outcome": "saved",
                "shot_type": "Open Play",
                "external_key_pass_event_id": "event-1",
            }
        )
        for index in (2, 3)
    ]

    result = classify_actions([pass_action, *shots])

    assert len(result.classifications) == 2
    assert all(row.status == "review" for row in result.classifications)
    assert all("multiple_shots_for_pass" in row.issues for row in result.classifications)


def test_empty_input_preserves_provenance() -> None:
    result = classify_actions([])

    assert result.classifications == ()
    assert result.resolution.events_sha256 == "a" * 64
    assert result.policy_version == "1.0.0"
