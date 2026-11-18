"""Existing creation, progression and control summaries adapted to scalar queries."""

from dataclasses import dataclass
from typing import cast

from football_analytics.analytics.catalogue import get_metric
from football_analytics.analytics.contracts import Coverage, MetricValue
from football_analytics.analytics.defensive_execution import Population
from football_analytics.ingestion.ball_control_statistics import (
    BallControlStatistics,
    build_ball_control_statistics,
)
from football_analytics.ingestion.creation_statistics import (
    CreationStatistics,
    build_creation_statistics,
)
from football_analytics.ingestion.movement_statistics import (
    MovementMetricName,
    MovementStatistics,
    build_movement_statistics,
)
from football_analytics.ingestion.player_scope_participation import PlayerScopeParticipation
from football_analytics.integrations.base import MatchDribbledPast, MatchLineup, MatchPressure
from football_analytics.integrations.statsbomb.ball_control import normalize_ball_control_incidents
from football_analytics.integrations.statsbomb.ball_recoveries import normalize_ball_recoveries
from football_analytics.integrations.statsbomb.blocks_and_clearances import (
    normalize_blocks_and_clearances,
)
from football_analytics.integrations.statsbomb.creation import normalize_creation_events
from football_analytics.integrations.statsbomb.dribbles import normalize_dribbles
from football_analytics.integrations.statsbomb.duels import normalize_duels
from football_analytics.integrations.statsbomb.events import normalize_events
from football_analytics.integrations.statsbomb.interceptions import normalize_interceptions
from football_analytics.integrations.statsbomb.passes import normalize_passes
from football_analytics.integrations.statsbomb.pressure_metadata import (
    normalize_event_pressure_metadata,
)
from football_analytics.integrations.statsbomb.pressures_and_fouls import (
    normalize_pressures_and_fouls,
)
from football_analytics.integrations.statsbomb.spatial import normalize_event_spatial

type Summary = CreationStatistics | MovementStatistics | BallControlStatistics


@dataclass
class FamilyContext:
    players: dict[str, Summary]
    teams: dict[str, Summary]
    population: dict[str, Population]
    review_reasons: tuple[str, ...] = ()


def _context(
    players: dict[str, Summary],
    teams: dict[str, Summary],
    scoped: list[PlayerScopeParticipation],
    lineups: list[MatchLineup],
    review_reasons: tuple[str, ...] = (),
) -> FamilyContext:
    roster = {p.external_player_id: p for p in lineups}
    presence = {p.external_player_id: p for p in scoped}
    if set(roster) != set(presence) or not set(players).issubset(roster):
        raise ValueError("Complementary summary differs from source roster")
    populations: dict[str, Population] = {}
    for pid, p in presence.items():
        observed = players.get(pid)
        if observed is not None:
            if (
                observed.model_dump(include={"external_team_id"})["external_team_id"]
                != p.external_team_id
            ):
                raise ValueError("Complementary summary team differs from participation")
            if p.status != "observed_in_scope":
                raise ValueError("Complementary actions without participation in scope")
            populations[pid] = "observed"
        else:
            populations[pid] = cast(
                Population,
                {
                    "observed_in_scope": "presence_confirmed",
                    "no_interval_in_scope": "not_applicable",
                    "unknown": "unknown",
                }[p.status],
            )
    return FamilyContext(players, teams, populations, review_reasons)


def prepare_complementary(
    events: object,
    *,
    match_id: int,
    events_sha256: str,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None,
    scoped: list[PlayerScopeParticipation],
    lineups: list[MatchLineup],
    families: frozenset[str],
) -> dict[str, FamilyContext]:
    result = {}
    # Keep calculations and provider-specific policies in their existing modules.
    if "creation" in families:
        creation = build_creation_statistics(
            normalize_creation_events(events, match_id=match_id),
            normalize_events(events, match_id=match_id),
            provider="statsbomb",
            match_id=str(match_id),
            events_sha256=events_sha256,
            available_periods=available_periods,
            periods=periods,
        )
        reasons = []
        if creation.reference_issues:
            reasons.append("creation_reference_issues_in_match")
        if creation.cross_scope_review_pairs:
            reasons.append("creation_cross_scope_pairs_in_match")
        result["creation"] = _context(
            {p.external_player_id: p for p in creation.players},
            {t.external_team_id: t for t in creation.teams},
            scoped,
            lineups,
            tuple(reasons),
        )
    if "movement" in families:
        movement = build_movement_statistics(
            normalize_event_spatial(events, match_id=match_id),
            normalize_passes(events, match_id=match_id),
            provider="statsbomb",
            match_id=str(match_id),
            events_sha256=events_sha256,
            available_periods=available_periods,
            periods=periods,
        )
        result["movement"] = _context(
            {p.external_player_id: p for p in movement.players},
            {t.external_team_id: t for t in movement.teams},
            scoped,
            lineups,
        )
    if "ball_control" in families:
        actions = [
            *normalize_ball_control_incidents(events, match_id=match_id),
            *normalize_passes(events, match_id=match_id),
            *normalize_dribbles(events, match_id=match_id),
            *normalize_ball_recoveries(events, match_id=match_id),
            *normalize_interceptions(events, match_id=match_id),
            *normalize_duels(events, match_id=match_id),
            *normalize_blocks_and_clearances(events, match_id=match_id),
            *(
                a
                for a in normalize_pressures_and_fouls(events, match_id=match_id)
                if isinstance(a, MatchPressure | MatchDribbledPast)
            ),
        ]
        control = build_ball_control_statistics(
            actions,
            normalize_event_spatial(events, match_id=match_id),
            normalize_event_pressure_metadata(events, match_id=match_id),
            provider="statsbomb",
            match_id=str(match_id),
            events_sha256=events_sha256,
            available_periods=available_periods,
            periods=periods,
        )
        result["ball_control"] = _context(
            {p.external_player_id: p for p in control.players},
            {t.external_team_id: t for t in control.teams},
            scoped,
            lineups,
        )
    return result


def complementary_value(
    identifier: str, context: FamilyContext, tid: str, pid: str | None
) -> MetricValue:
    definition = get_metric(identifier)
    summary = context.players.get(pid) if pid is not None else context.teams.get(tid)
    population = context.population[pid] if pid is not None else "presence_confirmed"
    if population in {"unknown", "not_applicable"}:
        return MetricValue(
            metric_id=identifier,
            unit=definition.unit,
            value=None,
            status="unknown" if population == "unknown" else "not_applicable",
            reasons=("population_" + population,),
            coverage=Coverage(
                source_complete=True, population=population, eligible_events=0, covered_events=0
            ),
        )
    count = n = 0
    reasons = list(context.review_reasons)
    if isinstance(summary, CreationStatistics):
        n = summary.counts.pass_count
        count = cast(int, getattr(summary.counts, identifier.rsplit(".", 1)[-1]))
        if summary.counts.review_pairs:
            reasons.append("creation_pairs_require_review")
        if summary.flagged_unlinked_pass_event_ids:
            reasons.append("creation_positive_flags_without_link")
    elif isinstance(summary, MovementStatistics):
        selection = dict(definition.selection)
        movement = summary.passes if selection["event_type"] == "Pass" else summary.carries
        counts = movement.metrics[cast(MovementMetricName, selection["metric"])]
        n = movement.event_count
        count = cast(int, getattr(counts, identifier.rsplit(".", 1)[-1]))
        if movement.geometry_unavailable_event_ids:
            reasons.append("movement_geometry_unavailable")
        if counts.unknown:
            reasons.append("movement_metric_unknown_classifications")
    elif isinstance(summary, BallControlStatistics):
        n = summary.event_count
        category = dict(definition.selection)["category"]
        count = sum(cell.event_count for cell in summary.cells if cell.category == category)
        if summary.zone_unavailable_event_ids:
            reasons.append("ball_control_zone_unavailable")
    return MetricValue(
        metric_id=identifier,
        unit=definition.unit,
        value=count,
        status="available",
        review_reasons=tuple(reasons),
        coverage=Coverage(
            source_complete=True, population=population, eligible_events=n, covered_events=n
        ),
    )
