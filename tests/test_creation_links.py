import pytest

from football_analytics.ingestion.creation_links import (
    CreationAction,
    CreationLinkResolution,
    resolve_creation_links,
)
from football_analytics.integrations.base import (
    MatchEvent,
    MatchEventRelations,
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
            "external_key_pass_event_id": pass_id,
        }
        | changes
    )


def base_event(action: MatchEvent) -> MatchEvent:
    return MatchEvent.model_validate(action.model_dump(include=set(MatchEvent.model_fields)))


def resolve(
    actions: list[CreationAction],
    extra: tuple[MatchEvent, ...] = (),
) -> CreationLinkResolution:
    return resolve_creation_links(
        actions,
        [base_event(action) for action in actions] + list(extra),
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
    )


def test_combines_specific_evidence_into_one_pair() -> None:
    result = resolve([shot_action(), pass_action()])

    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert pair.external_pass_event_id == "event-1"
    assert pair.external_shot_event_id == "event-2"
    assert pair.evidence == ("pass.assisted_shot_id", "shot.key_pass_id")
    assert pair.issues == ()
    assert result.reference_issues == ()
    assert result.unlinked_pass_event_ids == ()
    assert result.unlinked_shot_event_ids == ()


@pytest.mark.parametrize("direction", ["pass.assisted_shot_id", "shot.key_pass_id"])
def test_preserves_one_direction_without_inventing_reciprocity(
    direction: str,
) -> None:
    result = resolve(
        [
            pass_action(shot_id="event-2" if direction == "pass.assisted_shot_id" else None),
            shot_action(pass_id="event-1" if direction == "shot.key_pass_id" else None),
        ]
    )

    assert result.pairs[0].evidence == (direction,)
    assert result.pairs[0].issues == ()
    assert result.flagged_unlinked_pass_event_ids == ()


def test_generic_relationships_do_not_create_specific_pair() -> None:
    actions: list[CreationAction] = [
        pass_action(shot_id=None),
        shot_action(pass_id=None),
    ]
    catalogue = [
        MatchEventRelations.model_validate(
            base_event(actions[0]).model_dump() | {"related_event_ids": ("event-2",)}
        ),
        MatchEventRelations.model_validate(
            base_event(actions[1]).model_dump() | {"related_event_ids": ("event-1",)}
        ),
    ]

    result = resolve_creation_links(
        actions,
        catalogue,
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
    )

    assert result.pairs == ()
    assert result.flagged_unlinked_pass_event_ids == ("event-1",)


def test_reports_missing_target() -> None:
    result = resolve(
        [
            pass_action(shot_id="missing"),
            shot_action(pass_id=None),
        ]
    )

    assert result.pairs == ()
    assert result.reference_issues[0].reason == "missing_target"
    assert result.flagged_unlinked_pass_event_ids == ("event-1",)


def test_distinguishes_wrong_target_type_from_missing_target() -> None:
    other = MatchEvent.model_validate(header(3, "Pressure") | {"external_event_id": "other"})
    result = resolve(
        [pass_action(shot_id="other"), shot_action(pass_id=None)],
        extra=(other,),
    )

    assert result.reference_issues[0].reason == "wrong_target_type"
    assert result.reference_issues[0].observed_target_type == "Pressure"


def test_reports_self_reference() -> None:
    result = resolve([pass_action(shot_id="event-1")])

    assert result.pairs == ()
    assert result.reference_issues[0].reason == "self_reference"


def test_preserves_disagreement_as_multiple_candidate_passes() -> None:
    result = resolve(
        [
            pass_action(1, shot_id="event-3"),
            pass_action(2, shot_id="event-3"),
            shot_action(3, pass_id="event-1"),
        ]
    )

    assert len(result.pairs) == 2
    assert all("multiple_passes_for_shot" in pair.issues for pair in result.pairs)


def test_preserves_multiple_candidate_shots_for_pass() -> None:
    result = resolve(
        [
            pass_action(),
            shot_action(),
            shot_action(3),
        ]
    )

    assert len(result.pairs) == 2
    assert all("multiple_shots_for_pass" in pair.issues for pair in result.pairs)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"period": 2}, "period_mismatch"),
        ({"external_team_id": "771"}, "different_teams"),
        ({"external_team_id": None}, "missing_team_reference"),
        ({"external_player_id": None}, "missing_player_reference"),
        ({"external_player_id": "shooter"}, "same_player"),
        ({"timestamp_ms": 3000}, "timestamp_order_mismatch"),
    ],
)
def test_preserves_incompatible_pairs_with_diagnostics(
    changes: dict[str, object],
    expected: str,
) -> None:
    result = resolve([pass_action(**changes), shot_action()])

    assert expected in result.pairs[0].issues


def test_reports_pass_after_shot_in_source_order() -> None:
    result = resolve(
        [
            pass_action(2, shot_id="event-1"),
            shot_action(1, pass_id="event-2"),
        ]
    )

    assert "pass_not_before_shot" in result.pairs[0].issues


def test_accepts_equal_timestamps_with_correct_source_order() -> None:
    result = resolve(
        [
            pass_action(timestamp_ms=1000),
            shot_action(timestamp_ms=1000),
        ]
    )

    assert result.pairs[0].issues == ()


def test_leaves_pass_outcome_for_semantic_classification() -> None:
    result = resolve(
        [
            pass_action(outcome="incomplete"),
            shot_action(),
        ]
    )

    assert len(result.pairs) == 1
    assert result.pairs[0].issues == ()


def test_rejects_incomplete_detail_coverage() -> None:
    action = pass_action()
    with pytest.raises(ValueError, match="coverage is incomplete"):
        resolve_creation_links(
            [action],
            [base_event(action), base_event(shot_action())],
            provider="statsbomb",
            match_id="3869685",
            events_sha256="a" * 64,
        )


def test_rejects_different_headers() -> None:
    action = pass_action()
    changed = MatchPassCreation.model_validate(action.model_dump() | {"timestamp_ms": 9999})

    with pytest.raises(ValueError, match="headers differ"):
        resolve_creation_links(
            [changed],
            [base_event(action)],
            provider="statsbomb",
            match_id="3869685",
            events_sha256="a" * 64,
        )


def test_rejects_duplicate_catalogue_events() -> None:
    action = pass_action()
    original = base_event(action)

    with pytest.raises(ValueError, match="Duplicate catalogue"):
        resolve_creation_links(
            [action],
            [original, original],
            provider="statsbomb",
            match_id="3869685",
            events_sha256="a" * 64,
        )


def test_preserves_shootout_without_awarding_assists() -> None:
    result = resolve(
        [
            pass_action(period=5),
            shot_action(period=5),
        ]
    )

    assert len(result.pairs) == 1
    assert result.pairs[0].issues == ()
    assert result.events_sha256 == "a" * 64


def test_empty_input() -> None:
    result = resolve([])

    assert result.pairs == ()
    assert result.reference_issues == ()
    assert result.pass_event_ids == ()
    assert result.shot_event_ids == ()
