import pytest
from test_own_goal_evidence import event

from football_analytics.ingestion.own_goal_links import (
    resolve_own_goal_links,
)
from football_analytics.integrations.base import (
    MatchEvent,
    MatchEventRelations,
)


def opposite(index: int = 2, **changes):
    return event(
        index,
        event_type="Own Goal For",
        external_team_id="team-b",
        timestamp_ms=1000,
        **changes,
    )


def relations_for(events, links):
    return [
        MatchEventRelations.model_validate(
            item.model_dump(include=set(MatchEvent.model_fields))
            | {"related_event_ids": links.get(item.external_event_id)}
        )
        for item in events
    ]


def build(events, links):
    return resolve_own_goal_links(
        events,
        relations_for(events, links),
        provider="statsbomb",
        match_id="match-1",
        team_ids=("team-a", "team-b"),
    )


def test_resolves_bidirectional_pair_once() -> None:
    result = build(
        [event(1), opposite()],
        {
            "event-1": ("event-2",),
            "event-2": ("event-1",),
        },
    )

    assert len(result.pairs) == 1
    assert result.pairs[0].status == "usable"
    assert result.pairs[0].evidence == (
        "against_to_for",
        "for_to_against",
    )
    assert result.unpaired_event_ids == ()


def test_preserves_one_way_evidence() -> None:
    result = build(
        [event(1), opposite()],
        {"event-2": ("event-1",)},
    )

    assert result.pairs[0].evidence == ("for_to_against",)
    assert result.pairs[0].status == "usable"


def test_same_timestamp_without_link_does_not_merge_events() -> None:
    result = build([event(1), opposite()], {})

    assert result.pairs == ()
    assert result.unpaired_event_ids == ("event-1", "event-2")


def test_conflicting_teams_require_review() -> None:
    own_for = opposite().model_copy(update={"external_team_id": "team-a"})
    result = build(
        [event(1), own_for],
        {"event-1": ("event-2",)},
    )

    assert result.pairs[0].status == "review"
    assert "conflicting_teams" in result.pairs[0].issues


def test_multiple_counterparts_are_not_counted_as_resolved_goals() -> None:
    result = build(
        [event(1), opposite(), opposite(3)],
        {"event-1": ("event-2", "event-3")},
    )

    assert len(result.pairs) == 2
    assert all(pair.status == "review" for pair in result.pairs)
    assert all("ambiguous_counterpart" in pair.issues for pair in result.pairs)


def test_missing_reference_is_preserved() -> None:
    result = build(
        [event(1)],
        {"event-1": ("missing-event",)},
    )

    assert result.missing_reference_pairs == (("event-1", "missing-event"),)
    assert result.unpaired_event_ids == ("event-1",)


def test_requires_relation_coverage() -> None:
    with pytest.raises(ValueError, match="coverage differs"):
        resolve_own_goal_links(
            [event(1)],
            [],
            provider="statsbomb",
            match_id="match-1",
            team_ids=("team-a", "team-b"),
        )
