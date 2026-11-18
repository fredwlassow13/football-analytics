import importlib
from decimal import Decimal
from pathlib import Path

import pytest

from football_analytics.analytics.catalogue import METRICS, get_metric
from football_analytics.analytics.contracts import Coverage, MetricQuery, MetricValue
from football_analytics.analytics.resources import PENDING_CAPABILITIES, RESOURCES
from football_analytics.ingestion.defensive_statistics import FLAG_FIELDS


def test_defense_event_counts_and_every_flag_state_registered():
    from football_analytics.analytics.extended_catalogue import DEFENSIVE_TYPES

    assert set(DEFENSIVE_TYPES.values()) == set(FLAG_FIELDS)
    for slug, event_type in DEFENSIVE_TYPES.items():
        assert get_metric(f"defense.{slug}").selection == (("event_type", event_type),)
        for flag in FLAG_FIELDS[event_type]:
            for state in ("true", "false", "unknown"):
                m = get_metric(f"defense.{slug}.{flag}.{state}")
                assert m.implementation.endswith("." + state)
                assert ("flag", flag) in m.selection


def test_tackle_is_not_sliding_and_aerial_population_is_explicit():
    assert "sliding" in get_metric("duels.tackles.total").validation_notes
    assert get_metric("duels.aerial_lost").id != get_metric("aerial.lost").id
    assert get_metric("aerial.win_pct").denominator == "aerial.contested"
    with pytest.raises(ValueError):
        get_metric("defense.sliding_tackles")
    assert next(p for p in PENDING_CAPABILITIES if p.id == "defense.sliding_tackles").status == (
        "unsupported_by_current_adapter"
    )


def test_goalkeeper_cuts_and_diagnostics_are_distinct():
    for scope in ("counts", "penalty_counts", "non_penalty_counts", "unknown_penalty_counts"):
        m = get_metric(f"goalkeepers.{scope}.on_target_saves")
        assert m.kind == "metric" and m.selection == (("scope", scope),)
        assert get_metric(f"goalkeepers.{scope}.review_pairs").kind == "diagnostic"
    assert "own goals" in get_metric("goalkeepers.counts.shot_goals_conceded").validation_notes


def test_new_families_accept_existing_contract_with_execution_enabled():
    MetricQuery(
        match_ids=(123,),
        group_by="player",
        metrics=(
            "defense.interceptions",
            "aerial.win_pct",
            "duels.tackles.total",
            "goalkeepers.counts.on_target_saves",
            "dribbles.attempted",
            "creation.recorded_shot_assists",
            "movement.pass.countable_progression.true",
            "ball_control.miscontrol",
        ),
    )
    m = MetricValue(
        metric_id="aerial.win_pct",
        unit="percent",
        value=Decimal("66.67"),
        numerator=2,
        denominator=3,
        status="available",
        coverage=Coverage(
            source_complete=True, population="observed", eligible_events=3, covered_events=3
        ),
    )
    assert m.value == Decimal("66.67")
    assert all(m.execution_available for m in METRICS)


def test_resources_resolve_and_have_real_tests():
    root = Path(__file__).resolve().parents[1]
    assert len({r.id for r in RESOURCES}) == len(RESOURCES)
    for resource in RESOURCES:
        module, function = resource.implementation.rsplit(".", 1)
        assert callable(getattr(importlib.import_module(module), function))
        if resource.output_schema:
            module, name = resource.output_schema.rsplit(".", 1)
            assert getattr(importlib.import_module(module), name)
        assert all((root / t).is_file() for t in resource.tests), resource.id
        assert resource.execution_available == (
            resource.id
            in {
                "analysis.pass_connections",
                "analysis.defensive_map",
            }
        )


def test_structures_and_pending_capabilities_are_not_scalar_queries():
    for identifier in (
        "analysis.possession_fragments",
        "analysis.pass_connections",
        "possession.duration_and_percentage",
    ):
        with pytest.raises(ValueError):
            get_metric(identifier)
    assert any(r.kind == "visualization" for r in RESOURCES)
    assert any(r.kind == "diagnostic" for r in RESOURCES)


def test_catalogue_entry_types_and_ids_are_unambiguous():
    from football_analytics.analytics.catalogue import get_catalogue_entry

    entries = (*METRICS, *RESOURCES, *PENDING_CAPABILITIES)
    assert len(entries) == len({e.id for e in entries})
    assert get_catalogue_entry("analysis.defensive_map").kind == "visualization"
    assert get_catalogue_entry("defense.sliding_tackles").status == "unsupported_by_current_adapter"
    assert get_catalogue_entry("defense.interceptions").kind == "metric"


def test_xg_cut_cannot_present_partial_total_as_complete():
    for cut, scope in (
        ("penalty", "penalties"),
        ("non_penalty", "non_penalties"),
        ("unclassified_type", "unclassified_type"),
    ):
        metric_id = f"shots.{cut}.xg"
        assert get_metric(metric_id).selection == (("scope", scope),)
        with pytest.raises(ValueError, match="partial sum"):
            MetricValue(
                metric_id=metric_id,
                unit="expected_goals",
                value=Decimal("0.4"),
                status="incomplete",
                reasons=("missing_xg",),
                coverage=Coverage(
                    source_complete=True, population="observed", eligible_events=2, covered_events=1
                ),
            )
