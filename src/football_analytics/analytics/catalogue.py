"""Versioned definitions of existing calculations; registration is not execution."""

from football_analytics.analytics.capabilities import EXECUTOR_FAMILIES
from football_analytics.analytics.definitions import Group as Group
from football_analytics.analytics.definitions import MetricDefinition as MetricDefinition
from football_analytics.analytics.definitions import Unit as Unit
from football_analytics.analytics.extended_catalogue import additional_metrics
from football_analytics.analytics.resources import PENDING_CAPABILITIES as PENDING_CAPABILITIES
from football_analytics.analytics.resources import RESOURCES as RESOURCES
from football_analytics.analytics.resources import AnalysisResource, PendingCapability

CATALOGUE_VERSION = "1.6.0"

_COMMON_MISSING = (
    "Zero requires complete source coverage and an applicable population. "
    "An unobserved player without sufficient participation evidence remains unknown."
)


def _passes() -> list[MetricDefinition]:
    formulas = {
        "attempted": "completed + incomplete + out + offside + injury_clearance + unknown",
        "completed": "Count of passes with outcome complete",
        "incomplete": "Count of passes with outcome incomplete",
        "out": "Count of passes with outcome out",
        "offside": "Count of passes with outcome offside",
        "unknown": "Count of passes with outcome unknown",
        "injury_clearance": "Count of passes with outcome injury_clearance",
        "not_completed": "incomplete + out + offside + injury_clearance",
        "completion_pct": "100 * sum(completed) / sum(attempted); ROUND_HALF_UP to 2 decimals",
    }
    return [
        MetricDefinition(
            id=f"passes.{key}",
            name=f"Passes: {key}",
            family="passes",
            unit="percent" if key == "completion_pct" else "count",
            formula=formula,
            aggregation="ratio_of_sums" if key == "completion_pct" else "sum",
            denominator="passes.attempted (includes unknown outcomes)"
            if key == "completion_pct"
            else None,
            missing_policy=(
                _COMMON_MISSING + " No attempts: undefined percentage."
                if key == "completion_pct"
                else _COMMON_MISSING
            ),
            implementation="football_analytics.ingestion.pass_statistics.PlayerPassStatistics."
            + key,
            tests=("tests/test_pass_statistics.py", "tests/test_team_pass_statistics.py"),
            validation_notes="Existing player/team calculations; periods 1–4, excludes shootout. "
            "Shared execution support is indicated by execution_available.",
        )
        for key, formula in formulas.items()
    ]


def _shots() -> list[MetricDefinition]:
    formulas = {
        "total": "shots_on_target + saved_off_target + blocked + off_target + post + wayward",
        "goals": "Count of Shot events with outcome goal; excludes own goals",
        "shots_on_target": "goals + saved + saved_to_post",
        **{
            k: f"Count of Shot events with outcome {k}"
            for k in (
                "saved",
                "saved_to_post",
                "saved_off_target",
                "blocked",
                "off_target",
                "post",
                "wayward",
            )
        },
    }
    return [
        MetricDefinition(
            id=f"shots.{key}",
            name=f"Chutes: {key}",
            family="shots",
            unit="count",
            formula=formula,
            missing_policy=_COMMON_MISSING,
            implementation="football_analytics.ingestion.shot_statistics.PlayerShotStatistics."
            + ("shots" if key == "total" else key),
            tests=("tests/test_shot_statistics.py", "tests/test_team_shot_statistics.py"),
            validation_notes="Existing outcome-based calculations; excludes shootout. "
            "Shot goals alone do not represent a score including own goals.",
        )
        for key, formula in formulas.items()
    ]


def _xg() -> list[MetricDefinition]:
    specs: tuple[tuple[str, str, Unit, str, str], ...] = (
        (
            "shots.xg",
            "xG completo",
            "expected_goals",
            "Sum of provider xG only if all shots have xG",
            "ShotXgTotals.xg_total",
        ),
        (
            "shots.xg_available",
            "xG disponível",
            "expected_goals",
            "Sum of available provider xG values",
            "ShotXgTotals.available_xg_sum",
        ),
        (
            "shots.with_xg",
            "Chutes com xG",
            "count",
            "Count of selected shots with provider xG",
            "ShotXgTotals.shots_with_xg",
        ),
        (
            "shots.missing_xg",
            "Chutes sem xG",
            "count",
            "shots - shots_with_xg",
            "ShotXgTotals.missing_xg",
        ),
    )
    return [
        MetricDefinition(
            id=metric,
            name=name,
            family="xg",
            unit=unit,
            formula=formula,
            missing_policy=(
                "Missing xG is not zero. Complete total is unknown if any selected shot "
                "lacks xG. No shots means zero only with complete source/population coverage. "
                "Partial sums require incomplete status and coverage."
            ),
            implementation="football_analytics.ingestion.shot_xg_statistics." + implementation,
            tests=("tests/test_shot_xg_statistics.py", "tests/test_team_shot_xg_statistics.py"),
            validation_notes="StatsBomb-provided pre-shot xG, not a project model or PSxG. "
            "Shot type missing is retained as unclassified, not non-penalty.",
        )
        for metric, name, unit, formula, implementation in specs
    ]


def _xg_cuts() -> list[MetricDefinition]:
    result = []
    for label, scope in (
        ("penalty", "penalties"),
        ("non_penalty", "non_penalties"),
        ("unclassified_type", "unclassified_type"),
    ):
        for metric in _xg():
            result.append(
                metric.model_copy(
                    update={
                        "id": f"shots.{label}.{metric.id.removeprefix('shots.')}",
                        "name": f"{metric.name} ({label})",
                        "selection": (("scope", scope),),
                    }
                )
            )
    return result


METRICS: tuple[MetricDefinition, ...] = (
    *_passes(),
    *_shots(),
    *_xg(),
    *_xg_cuts(),
    *additional_metrics(),
    MetricDefinition(
        id="participation.elapsed_on_field_ms",
        name="Tempo decorrido em campo",
        family="participation",
        unit="milliseconds",
        formula="Sum of clipped on-field interval durations in selected periods",
        groups=("player",),
        missing_policy="No participation evidence means unknown duration, not zero.",
        implementation="football_analytics.ingestion.player_scope_participation.scope_player_participation",
        tests=("tests/test_player_scope_participation.py", "tests/test_participation_storage.py"),
        validation_notes="Underlying elapsed_on_field_ms formula 1.0.0. Includes stoppages within "
        "periods, excludes between-period breaks and shootout. Not ball-in-play time "
        "or conventional per-90 exposure. Team sum is not team match duration.",
    ),
)


# Preserve definition IDs/versions; capability changes are catalogue-level metadata.
METRICS = tuple(
    metric.model_copy(update={"execution_available": metric.family in EXECUTOR_FAMILIES})
    for metric in METRICS
)


def get_metric(metric_id: str) -> MetricDefinition:
    for metric in METRICS:
        if metric.id == metric_id:
            return metric
    raise ValueError(f"Unknown metric: {metric_id}")


def get_catalogue_entry(identifier: str) -> MetricDefinition | AnalysisResource | PendingCapability:
    entries: tuple[MetricDefinition | AnalysisResource | PendingCapability, ...] = (
        *METRICS,
        *RESOURCES,
        *PENDING_CAPABILITIES,
    )
    for entry in entries:
        if entry.id == identifier:
            return entry
    raise ValueError(f"Unknown catalogue entry: {identifier}")
