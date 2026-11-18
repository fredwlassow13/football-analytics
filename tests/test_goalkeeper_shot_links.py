import pytest

from football_analytics.ingestion.goalkeeper_shot_links import (
    GoalkeeperShotResolution,
    resolve_goalkeeper_shot_links,
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


def keeper(
    index: int = 2,
    *,
    related: tuple[str, ...] | None = None,
    **changes: object,
) -> MatchGoalkeeperAction:
    return MatchGoalkeeperAction.model_validate(
        header(index, "Goal Keeper")
        | {
            "goalkeeper_type": "shot_saved",
            "outcome": "success",
            "related_event_ids": related,
        }
        | changes
    )


def shot(index: int = 1, **changes: object) -> MatchShot:
    return MatchShot.model_validate(header(index, "Shot") | {"outcome": "saved"} | changes)


def relation(
    event: MatchEvent,
    targets: tuple[str, ...] | None,
) -> MatchEventRelations:
    return MatchEventRelations.model_validate(
        event.model_dump(include=set(MatchEvent.model_fields)) | {"related_event_ids": targets}
    )


def resolve(
    keepers: list[MatchGoalkeeperAction],
    shots: list[MatchShot],
    *,
    shot_links: dict[str, tuple[str, ...]] | None = None,
    extra: tuple[MatchEventRelations, ...] = (),
) -> GoalkeeperShotResolution:
    links = {} if shot_links is None else shot_links
    relations = (
        [relation(action, action.related_event_ids) for action in keepers]
        + [relation(action, links.get(action.external_event_id)) for action in shots]
        + list(extra)
    )

    return resolve_goalkeeper_shot_links(
        keepers,
        shots,
        relations,
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
    )


def test_combines_reciprocal_evidence_into_one_pair() -> None:
    result = resolve(
        [keeper(related=("event-1",))],
        [shot()],
        shot_links={"event-1": ("event-2",)},
    )

    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert pair.external_goalkeeper_event_id == "event-2"
    assert pair.external_shot_event_id == "event-1"
    assert pair.evidence == ("goalkeeper_to_shot", "shot_to_goalkeeper")
    assert pair.issues == ()
    assert result.source_issues == ()
    assert result.unlinked_goalkeeper_event_ids == ()
    assert result.unlinked_shot_event_ids == ()
    assert result.events_sha256 == "a" * 64


@pytest.mark.parametrize("direction", ["goalkeeper_to_shot", "shot_to_goalkeeper"])
def test_preserves_one_direction_without_inventing_reciprocity(
    direction: str,
) -> None:
    result = resolve(
        [keeper(related=("event-1",) if direction == "goalkeeper_to_shot" else None)],
        [shot()],
        shot_links=({"event-1": ("event-2",)} if direction == "shot_to_goalkeeper" else None),
    )

    assert len(result.pairs) == 1
    assert result.pairs[0].evidence == (direction,)
    assert result.pairs[0].issues == ()


def test_does_not_link_by_timestamp_proximity() -> None:
    result = resolve(
        [keeper(timestamp_ms=1000)],
        [shot(timestamp_ms=1000)],
    )

    assert result.pairs == ()
    assert result.unlinked_goalkeeper_event_ids == ("event-2",)
    assert result.unlinked_shot_event_ids == ("event-1",)


def test_flags_multiple_shots_for_one_goalkeeper_event() -> None:
    result = resolve(
        [keeper(related=("event-1", "event-3"))],
        [shot(1), shot(3)],
    )

    assert len(result.pairs) == 2
    assert all("multiple_shots_for_goalkeeper" in pair.issues for pair in result.pairs)


def test_flags_multiple_goalkeeper_events_for_one_shot() -> None:
    result = resolve(
        [
            keeper(2, related=("event-1",)),
            keeper(3, related=("event-1",)),
        ],
        [shot()],
    )

    assert len(result.pairs) == 2
    assert all("multiple_goalkeepers_for_shot" in pair.issues for pair in result.pairs)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"period": 2}, "period_mismatch"),
        ({"external_team_id": "779"}, "same_team"),
        ({"external_player_id": "shooter-player"}, "same_player"),
        ({"external_team_id": None}, "missing_team_reference"),
        ({"external_player_id": None}, "missing_player_reference"),
    ],
)
def test_preserves_incompatible_pairs_with_issues(
    changes: dict[str, object],
    expected: str,
) -> None:
    result = resolve(
        [keeper(related=("event-1",), **changes)],
        [shot()],
    )

    assert len(result.pairs) == 1
    assert expected in result.pairs[0].issues


def test_reports_duplicate_target_without_duplicate_pair() -> None:
    result = resolve(
        [keeper(related=("event-1", "event-1"))],
        [shot()],
    )

    assert len(result.pairs) == 1
    assert len(result.source_issues) == 1
    assert result.source_issues[0].reason == "duplicate_related_target"


def test_reports_missing_target_and_ignores_other_event_types() -> None:
    other = MatchEventRelations.model_validate(header(3, "Pressure") | {"related_event_ids": None})
    result = resolve(
        [keeper(related=("missing", "event-3"))],
        [shot()],
        extra=(other,),
    )

    assert result.pairs == ()
    assert len(result.source_issues) == 1
    assert result.source_issues[0].reason == "missing_related_target"
    assert result.source_issues[0].external_target_event_id == "missing"


def test_reports_self_reference() -> None:
    result = resolve(
        [keeper(related=("event-2",))],
        [],
    )

    assert result.pairs == ()
    assert result.source_issues[0].reason == "self_reference"


def test_preserves_shootout_links() -> None:
    result = resolve(
        [keeper(related=("event-1",), period=5)],
        [shot(period=5)],
    )

    assert len(result.pairs) == 1
    assert result.pairs[0].issues == ()


def test_rejects_different_detail_and_relation_headers() -> None:
    action = keeper()
    original = relation(action, None)
    changed = MatchEventRelations.model_validate(original.model_dump() | {"timestamp_ms": 9999})

    with pytest.raises(ValueError, match="headers differ"):
        resolve_goalkeeper_shot_links(
            [action],
            [],
            [changed],
            provider="statsbomb",
            match_id="3869685",
            events_sha256="a" * 64,
        )


def test_rejects_different_goalkeeper_relationships() -> None:
    action = keeper(related=("event-1",))

    with pytest.raises(ValueError, match="relationships differ"):
        resolve_goalkeeper_shot_links(
            [action],
            [shot()],
            [relation(action, None), relation(shot(), None)],
            provider="statsbomb",
            match_id="3869685",
            events_sha256="a" * 64,
        )


def test_rejects_incomplete_normalized_detail_coverage() -> None:
    with pytest.raises(ValueError, match="coverage is incomplete"):
        resolve_goalkeeper_shot_links(
            [],
            [],
            [relation(shot(), None)],
            provider="statsbomb",
            match_id="3869685",
            events_sha256="a" * 64,
        )


def test_rejects_duplicate_relation_sources() -> None:
    original = relation(shot(), None)

    with pytest.raises(ValueError, match="Duplicate relation"):
        resolve_goalkeeper_shot_links(
            [],
            [shot()],
            [original, original],
            provider="statsbomb",
            match_id="3869685",
            events_sha256="a" * 64,
        )


def test_empty_input() -> None:
    result = resolve([], [])

    assert result.pairs == ()
    assert result.source_issues == ()
    assert result.goalkeeper_event_ids == ()
    assert result.shot_event_ids == ()
