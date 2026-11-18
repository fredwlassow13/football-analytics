"""Definitions backed by existing defensive and complementary analysis models."""

from football_analytics.analytics.definitions import MetricDefinition
from football_analytics.ingestion.ball_control_evidence import EvidenceCategory
from football_analytics.ingestion.defensive_statistics import FLAG_FIELDS
from football_analytics.ingestion.movement_statistics import METRICS as MOVEMENT_METRICS

# Stable labels are explicit; native breakdowns remain structured catalogue resources.
DEFENSIVE_TYPES = {
    "ball_recoveries": "Ball Recovery",
    "blocks": "Block",
    "clearances": "Clearance",
    "dribbled_past": "Dribbled Past",
    "duels": "Duel",
    "fouls_committed": "Foul Committed",
    "fouls_won": "Foul Won",
    "interceptions": "Interception",
    "pressures": "Pressure",
}


def _definition(
    metric_id: str,
    family: str,
    model: str,
    member: str,
    *,
    formula: str,
    tests: tuple[str, ...],
    notes: str,
    selection: tuple[tuple[str, str], ...] = (),
    diagnostic: bool = False,
) -> MetricDefinition:
    return MetricDefinition.model_validate(
        {
            "id": metric_id,
            "name": metric_id,
            "family": family,
            "unit": "count",
            "formula": formula,
            "implementation": f"football_analytics.ingestion.{model}.{member}",
            "tests": tests,
            "validation_notes": notes,
            "selection": selection,
            "kind": "diagnostic" if diagnostic else "metric",
            "missing_policy": "Unknown flags/outcomes are not false or successful actions. "
            "Zero requires full source coverage and an applicable population. "
            "An observed-event aggregate alone does not establish roster-wide zeros.",
        }
    )


def additional_metrics() -> tuple[MetricDefinition, ...]:
    result = []
    result.append(
        _definition(
            "defense.unique_sources",
            "defense",
            "defensive_summary.PlayerDefensiveSummary",
            "external_event_ids",
            formula="Length of union of action and aerial source event IDs",
            tests=("tests/test_defensive_summary.py",),
            notes="Unique source union; shared clearance/aerial evidence is counted once. "
            "Do not add overlapping category totals to reproduce this count.",
        )
    )
    tests = ("tests/test_defensive_statistics.py", "tests/test_team_defensive_statistics.py")
    notes = (
        "Source-event counts, periods 1–4. Includes failed and uncertain actions; "
        "not recoveries won."
    )
    for slug, event_type in DEFENSIVE_TYPES.items():
        selection = (("event_type", event_type),)
        result.append(
            _definition(
                f"defense.{slug}",
                "defense",
                "defensive_statistics.DefensiveActionStatistics",
                "event_count",
                formula=f"Count of selected {event_type} source events",
                tests=tests,
                notes=notes,
                selection=selection,
            )
        )
        for flag in FLAG_FIELDS[event_type]:
            for state in ("true", "false", "unknown"):
                result.append(
                    _definition(
                        f"defense.{slug}.{flag}.{state}",
                        "defense",
                        "defensive_statistics.FlagCounts",
                        state,
                        formula=f"Count of {flag}={state}",
                        tests=tests,
                        notes=notes + " States are mutually exclusive within this flag only.",
                        selection=(*selection, ("flag", flag)),
                        diagnostic=state == "unknown",
                    )
                )
    for member in (
        "lost",
        "won",
        "lost_in_play",
        "lost_out",
        "success",
        "success_in_play",
        "success_out",
        "total",
    ):
        result.append(
            _definition(
                f"duels.tackles.{member}",
                "duels",
                "duel_statistics.TackleOutcomeCounts",
                member,
                formula="Sum of the seven recorded tackle outcomes"
                if member == "total"
                else f"Count of tackle outcome {member}",
                tests=("tests/test_duel_statistics.py", "tests/test_team_duel_statistics.py"),
                notes="Tackle does not identify sliding tackles. Won and success remain distinct; "
                "no binary ground-duel success policy is inferred.",
            )
        )
    for member in ("aerial_lost", "total_duel_events"):
        result.append(
            _definition(
                f"duels.{member}",
                "duels",
                "duel_statistics.PlayerDuelStatistics",
                member,
                formula="tackles.total + aerial_lost"
                if member == "total_duel_events"
                else "Count of Duel/Aerial Lost events",
                tests=("tests/test_duel_statistics.py",),
                notes="Duel-source population only. "
                "Aerial wins come from other event types; "
                "use aerial metrics for the full breakdown.",
            )
        )
    for family, model, members, ratio, numerator, denominator, tests in (
        (
            "aerial",
            "aerial_statistics.PlayerAerialStatistics",
            ("won", "lost", "contested", "win_pct"),
            "win_pct",
            "won",
            "contested",
            ("tests/test_aerial_statistics.py", "tests/test_team_aerial_statistics.py"),
        ),
        (
            "dribbles",
            "dribble_statistics.PlayerDribbleStatistics",
            ("completed", "incomplete", "attempted", "completion_pct"),
            "completion_pct",
            "completed",
            "attempted",
            ("tests/test_dribble_statistics.py", "tests/test_team_dribble_statistics.py"),
        ),
    ):
        for member in members:
            entry = _definition(
                f"{family}.{member}",
                family,
                model,
                member,
                formula=(
                    f"100 * sum({numerator}) / sum({denominator}); ROUND_HALF_UP to 2 decimals"
                    if member == ratio
                    else f"Existing {member} count/property"
                ),
                tests=tests,
                notes="Periods 1–4; zero denominator gives undefined percentage. "
                "Aerial wins require explicit source evidence; Dribble is not Carry.",
            )
            if member == ratio:
                entry = entry.model_copy(
                    update={
                        "unit": "percent",
                        "aggregation": "ratio_of_sums",
                        "denominator": f"{family}.{denominator}",
                    }
                )
            result.append(entry)
    for scope in ("counts", "penalty_counts", "non_penalty_counts", "unknown_penalty_counts"):
        for member in (
            "pair_count",
            "shot_goals_conceded",
            "on_target_saves",
            "off_target_saves",
            "other_shots",
            "review_pairs",
        ):
            result.append(
                _definition(
                    f"goalkeepers.{scope}.{member}",
                    "goalkeepers",
                    "player_goalkeeper_statistics.GoalkeeperShotCounts",
                    member,
                    formula=f"Existing classification count {member} in {scope}",
                    tests=(
                        "tests/test_player_goalkeeper_statistics.py",
                        "tests/test_goalkeeper_statistics.py",
                    ),
                    notes="Requires explicit goalkeeper–shot links; review pairs are not saves. "
                    "Shot goals conceded exclude own goals; not a complete scoreboard. "
                    "Other interventions remain in the structured action breakdown.",
                    selection=(("scope", scope),),
                    diagnostic=member in {"pair_count", "review_pairs"},
                )
            )
    for member in (
        "pass_count",
        "candidate_pairs",
        "usable_pairs",
        "review_pairs",
        "recorded_shot_assists",
        "recorded_goal_assists",
        "passes_with_any_recorded_flag",
        "passes_with_both_recorded_flags",
        "usable_pairs_without_positive_flag",
    ):
        result.append(
            _definition(
                f"creation.{member}",
                "creation",
                "creation_statistics.CreationCounts",
                member,
                formula=f"Existing creation count {member}",
                tests=("tests/test_creation_statistics.py",),
                notes="Source flags and explicit pass–shot links; no inferred assists or xA. "
                "Scope is pass period; candidate and review pairs are diagnostics.",
                diagnostic=member
                not in {
                    "recorded_shot_assists",
                    "recorded_goal_assists",
                    "passes_with_any_recorded_flag",
                    "passes_with_both_recorded_flags",
                },
            )
        )
    for event_type in ("Pass", "Carry"):
        for metric in MOVEMENT_METRICS:
            for state in ("true", "false", "unknown"):
                result.append(
                    _definition(
                        f"movement.{event_type.lower()}.{metric}.{state}",
                        "movement",
                        "movement_statistics.MovementMetric",
                        state,
                        formula=f"Count of {event_type} with {metric}={state}",
                        tests=(
                            "tests/test_movement_statistics.py",
                            "tests/test_movement_progression.py",
                        ),
                        notes="Policy project_dx10_goal_distance25: dx>=10 "
                        "and goal distance reduction>=25%. "
                        "Completed passes are countable; "
                        "geometric progression alone is not a success. "
                        "StatsBomb coordinates, not metres or measured distance travelled.",
                        selection=(("event_type", event_type), ("metric", metric)),
                        diagnostic=state != "true"
                        or metric in {"eligible_for_count", "geometrically_progressive"},
                    )
                )
    # Importing Literal arguments avoids inventing categories beyond the implemented policy.
    from typing import get_args

    for category in get_args(EvidenceCategory):
        result.append(
            _definition(
                f"ball_control.{category}",
                "ball_control",
                "ball_control_statistics.BallControlCell",
                "event_count",
                formula=f"Sum event_count across disjoint cells with category={category}",
                tests=(
                    "tests/test_ball_control_statistics.py",
                    "tests/test_ball_control_evidence.py",
                ),
                notes="Observed control evidence, not effective possession wins/losses. "
                "Cells also retain "
                "outcome, thirds/channels and true/false/unknown pressure flags.",
                selection=(("category", category),),
            )
        )
    return tuple(result)
