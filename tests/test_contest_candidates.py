import pytest
from test_possession_views import sample

from football_analytics.ingestion.contest_candidates import build_contest_candidates
from football_analytics.ingestion.possession_views import build_possession_views
from football_analytics.integrations.base import MatchEventContext


def event(index, kind="Duel", time=50000, **changes):
    return MatchEventContext.model_validate(
        {
            "provider": "statsbomb",
            "external_match_id": "1",
            "external_event_id": f"event-{index}",
            "source_index": index,
            "period": 1,
            "timestamp_ms": time,
            "event_type": kind,
            "external_team_id": "a",
            "external_possession_team_id": "a",
            "possession_sequence": 1,
            **changes,
        }
    )


def build(events, **options):
    views = build_possession_views(sample()[0])
    return build_contest_candidates(events, views, events_sha256="a" * 64, **options)


def test_candidate_has_review_context_and_is_never_accepted():
    report = build([event(1, "Pass", 49000), event(2), event(3, "Carry", 51000)])
    candidate = report.candidates[0]
    assert candidate.suggested_start_ms == 50000
    assert candidate.suggested_end_ms == 51000
    assert candidate.preceding_control_proxy.external_event_id == "event-1"
    assert candidate.following_boundary_proxy.external_event_id == "event-3"
    assert len(candidate.context_events) == 3
    assert not candidate.overlaps_known_time
    assert candidate.review_status == "pending"
    assert report.accepted_contest_windows == ()
    assert "boundaries_require_independent_review" in candidate.issues


def test_fifty_fifty_is_included_and_shootout_excluded():
    report = build([event(1, "50/50"), event(2, "Carry", 51000), event(3, period=5)])
    assert report.candidates[0].seed.event_type == "50/50"
    assert report.excluded_shootout_seed_ids == ("event-3",)
    assert len(report.candidates) == 1


def test_seed_without_next_boundary_has_no_suggestion():
    candidate = build([event(1)]).candidates[0]
    assert candidate.suggested_end_ms is None
    assert "missing_following_boundary_proxy" in candidate.issues


def test_boundary_in_other_period_is_not_used():
    report = build([event(1), event(2, "Carry", 1000, period=2)])
    assert report.candidates[0].following_boundary_proxy is None


def test_stop_proxy_limits_suggestion_before_next_control():
    c = build([event(1), event(2, "Offside", 50500), event(3, "Pass", 51000)]).candidates[0]
    assert c.suggested_end_ms == 50500
    assert c.following_boundary_proxy.event_type == "Offside"


def test_conflict_with_existing_possession_is_flagged_not_reclassified():
    views = build_possession_views(sample()[0])
    original = views.model_dump_json()
    report = build_contest_candidates(
        [event(1, time=20000), event(2, "Pass", 21000)], views, events_sha256="a" * 64
    )
    assert report.candidates[0].overlaps_known_time
    assert report.candidates[0].interval_overlaps[0].category == "team"
    assert views.model_dump_json() == original


def test_duplicate_perspectives_are_flagged_as_overlapping_candidates():
    report = build([event(1), event(2), event(3, "Pass", 51000)])
    assert len(report.candidates) == 2
    for c in report.candidates:
        assert len(c.overlapping_candidate_ids) == 1
        assert "overlapping_candidates" in c.issues
    assert not report.accepted_contest_windows


@pytest.mark.parametrize(
    "end,issue",
    [
        (50000, "nonpositive_candidate_window"),
        (49000, "timestamp_regression"),
        (56000, "candidate_window_exceeds_cap"),
    ],
)
def test_invalid_temporal_bracket_has_no_suggestion(end, issue):
    c = build([event(1), event(2, "Pass", end)]).candidates[0]
    assert c.suggested_start_ms is None and c.suggested_end_ms is None
    assert issue in c.issues


def test_intermediate_clock_regression_cannot_be_hidden_by_later_boundary():
    c = build([event(1), event(2, "Pressure", 49000), event(3, "Pass", 51000)]).candidates[0]
    assert c.suggested_end_ms is None
    assert "timestamp_regression" in c.issues


def test_source_index_gap_blocks_suggestion():
    c = build([event(1), event(3, "Pass", 51000)]).candidates[0]
    assert c.suggested_end_ms is None
    assert "source_index_gap" in c.issues


def test_context_radius_limits_only_context_not_boundary_search():
    c = build(
        [event(1), event(2, "Pressure", 50500), event(3, "Pass", 51000)], context_radius=0
    ).candidates[0]
    assert len(c.context_events) == 1
    assert c.suggested_end_ms == 51000


@pytest.mark.parametrize(
    "options",
    [
        {"max_window_ms": 0},
        {"max_window_ms": True},
        {"context_radius": -1},
        {"context_radius": True},
    ],
)
def test_invalid_options_are_rejected(options):
    with pytest.raises(ValueError):
        build([event(1)], **options)


def test_duplicate_source_wrong_identity_hash_or_clock_is_rejected():
    for events in ([event(1), event(1)], [event(1, provider="other")], [event(1, time=100001)]):
        with pytest.raises(ValueError):
            build(events)
    with pytest.raises(ValueError, match="hash"):
        build_contest_candidates(
            [event(1)], build_possession_views(sample()[0]), events_sha256="b" * 64
        )


def test_out_of_scope_seeds_are_recorded():
    report = build([event(1, period=2)])
    assert not report.candidates
    assert report.out_of_scope_seed_ids == ("event-1",)


def test_opposing_control_is_not_skipped_to_wait_for_possession_label():
    c = build(
        [
            event(1),
            event(2, "Carry", 50300, external_team_id="b"),
            event(3, "Pass", 52000),
        ]
    ).candidates[0]
    assert c.suggested_end_ms == 50300
    assert c.following_boundary_proxy.external_event_id == "event-2"
    assert c.following_possession_aligned_proxy.external_event_id == "event-3"
    assert [e.external_event_id for e in c.control_proxies_before_aligned_boundary] == ["event-2"]
    assert "boundary_possession_team_disagreement" in c.issues


def test_same_timestamp_opposing_control_does_not_create_fictitious_duration():
    c = build(
        [event(1), event(2, "Pass", 50000, external_team_id="b"), event(3, "Pass", 52000)]
    ).candidates[0]
    assert c.suggested_end_ms is None
    assert c.following_boundary_proxy.external_event_id == "event-2"
    assert "nonpositive_candidate_window" in c.issues


def test_known_stoppage_limits_window_and_preserves_original_boundary():
    c = build([event(1, time=89000), event(2, "Pass", 91000)]).candidates[0]
    assert c.suggested_end_ms == 90000
    assert c.unclipped_end_ms == 91000
    assert c.known_stoppage_limit_ms == 90000
    assert c.crosses_known_stoppage
    assert not c.overlaps_known_time
    assert "known_stoppage_limits_window" in c.issues
    assert all(o.category == "unknown" for o in c.interval_overlaps)


def test_seed_inside_known_stoppage_has_no_proposed_duration():
    c = build([event(1, time=91000), event(2, "Pass", 92000)]).candidates[0]
    assert c.suggested_start_ms is None and c.suggested_end_ms is None
    assert c.crosses_known_stoppage


def test_fractional_ms_stop_boundary_is_preserved():
    from decimal import Decimal

    views = build_possession_views(sample()[0])
    changed = tuple(
        i.model_copy(
            update={
                "end_ms": Decimal("90000.5") if i.category == "unknown" else i.end_ms,
                "start_ms": Decimal("90000.5") if i.category == "stopped" else i.start_ms,
            }
        )
        for i in views.intervals
    )
    c = build_contest_candidates(
        [event(1, time=89000), event(2, "Pass", 91000)],
        views.model_copy(update={"intervals": changed}),
        events_sha256="a" * 64,
    ).candidates[0]
    assert c.suggested_end_ms == Decimal("90000.5")


def test_reciprocal_same_timestamp_fifty_fifty_gets_perspective_links():
    from football_analytics.ingestion.contest_candidates import EventAnnotation

    report = build(
        [event(1, "50/50"), event(2, "50/50", external_team_id="b"), event(3, "Pass", 51000)],
        event_annotations={
            "event-1": EventAnnotation(
                event_type="50/50", outcome="Lost", related_event_ids=("event-2",)
            ),
            "event-2": EventAnnotation(
                event_type="50/50", outcome="Success To Opposition", related_event_ids=("event-1",)
            ),
        },
    )
    assert report.candidates[0].paired_perspective_candidate_ids == ("1:event-2",)
    assert report.candidates[1].paired_perspective_candidate_ids == ("1:event-1",)
    assert report.candidates[0].seed.outcome == "Lost"
    assert not report.accepted_contest_windows


def test_distinct_duels_are_not_deduplicated_even_with_reciprocal_relation():
    from football_analytics.ingestion.contest_candidates import EventAnnotation

    report = build(
        [event(1), event(2), event(3, "Pass", 51000)],
        event_annotations={
            "event-1": EventAnnotation(
                event_type="Duel", duel_type="Tackle", outcome="Won", related_event_ids=("event-2",)
            ),
            "event-2": EventAnnotation(
                event_type="Duel",
                duel_type="Tackle",
                outcome="Lost In Play",
                related_event_ids=("event-1",),
            ),
        },
    )
    assert all(not c.paired_perspective_candidate_ids for c in report.candidates)
    assert report.candidates[0].seed.duel_type == "Tackle"
    assert report.candidates[0].seed.outcome == "Won"


def test_one_way_relation_is_not_enough_to_pair_perspectives():
    from football_analytics.ingestion.contest_candidates import EventAnnotation

    report = build(
        [event(1, "50/50"), event(2, "50/50", external_team_id="b")],
        event_annotations={
            "event-1": EventAnnotation(event_type="50/50", related_event_ids=("event-2",)),
        },
    )
    assert all(not c.paired_perspective_candidate_ids for c in report.candidates)


def test_annotation_with_wrong_source_id_or_type_is_rejected():
    from football_analytics.ingestion.contest_candidates import EventAnnotation

    for annotations in (
        {"missing": EventAnnotation(event_type="Duel")},
        {"event-1": EventAnnotation(event_type="Pass")},
    ):
        with pytest.raises(ValueError, match="Annotation"):
            build([event(1)], event_annotations=annotations)


def test_context_snapshots_include_raw_outcome_and_related_ids():
    from football_analytics.ingestion.contest_candidates import EventAnnotation

    c = build(
        [event(1), event(2, "Pass", 51000)],
        event_annotations={
            "event-1": EventAnnotation(
                event_type="Duel",
                duel_type="Aerial Lost",
                source_duration_seconds=0.0,
                related_event_ids=("event-2",),
            ),
        },
    ).candidates[0]
    assert c.context_events[0].duel_type == "Aerial Lost"
    assert c.context_events[0].related_event_ids == ("event-2",)
    assert c.seed.source_duration_seconds == 0.0
    assert c.suggested_end_ms == 51000
