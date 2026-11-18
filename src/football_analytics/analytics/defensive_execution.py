"""Adapters from existing defensive/keeper summaries to shared scalar results."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, cast

from football_analytics.analytics.catalogue import get_metric
from football_analytics.analytics.contracts import Coverage, MetricValue
from football_analytics.ingestion.defensive_roster import build_defensive_roster
from football_analytics.ingestion.defensive_summary import (
    PlayerDefensiveSummary,
    TeamDefensiveSummary,
    build_defensive_summary,
)
from football_analytics.ingestion.goalkeeper_shot_classification import EXPECTED_KEEPER_TYPES
from football_analytics.ingestion.goalkeeper_statistics import (
    GoalkeeperStatisticsResult,
    TeamGoalkeeperStatistics,
    build_goalkeeper_statistics,
)
from football_analytics.ingestion.match_participation import PlayerParticipation
from football_analytics.ingestion.player_goalkeeper_statistics import (
    GoalkeeperShotCounts,
    PlayerGoalkeeperStatistics,
)
from football_analytics.ingestion.player_scope_participation import PlayerScopeParticipation
from football_analytics.ingestion.team_tactical_timeline import build_team_tactical_timeline
from football_analytics.integrations.base import MatchLineup
from football_analytics.integrations.statsbomb.aerial_duels import normalize_aerial_duels
from football_analytics.integrations.statsbomb.ball_recoveries import normalize_ball_recoveries
from football_analytics.integrations.statsbomb.blocks_and_clearances import (
    normalize_blocks_and_clearances,
)
from football_analytics.integrations.statsbomb.duels import normalize_duels
from football_analytics.integrations.statsbomb.event_relations import normalize_event_relations
from football_analytics.integrations.statsbomb.goalkeepers import normalize_goalkeeper_actions
from football_analytics.integrations.statsbomb.interceptions import normalize_interceptions
from football_analytics.integrations.statsbomb.period_clock import (
    PeriodBoundary,
)
from football_analytics.integrations.statsbomb.pressures_and_fouls import (
    normalize_pressures_and_fouls,
)
from football_analytics.integrations.statsbomb.shots import normalize_shots
from football_analytics.integrations.statsbomb.tactics import normalize_tactical_snapshots

Population = Literal["observed", "presence_confirmed", "unknown", "not_applicable"]


@dataclass
class DefensiveExecution:
    players: dict[str, PlayerDefensiveSummary]
    teams: dict[str, TeamDefensiveSummary]
    population: dict[str, Population]
    keepers: GoalkeeperStatisticsResult | None
    roles: dict[str, Population]


def _elapsed(period: int, milliseconds: int, clock: list[PeriodBoundary]) -> int:
    boundary = next(p for p in clock if p.period == period)
    if not 0 <= milliseconds <= boundary.duration_ms:
        raise ValueError("Tactical snapshot outside its period")
    return boundary.start_elapsed_ms + milliseconds


def prepare_defense(
    events: object,
    *,
    match_id: int,
    events_sha256: str,
    lineups_sha256: str,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None,
    lineups: list[MatchLineup],
    players: list[PlayerParticipation],
    scoped: list[PlayerScopeParticipation],
    clock: list[PeriodBoundary],
    needs_defense: bool,
    needs_keepers: bool,
) -> DefensiveExecution:
    result = DefensiveExecution({}, {}, {}, None, {})
    if needs_defense:
        actions = [
            *normalize_duels(events, match_id=match_id),
            *normalize_interceptions(events, match_id=match_id),
            *normalize_ball_recoveries(events, match_id=match_id),
            *normalize_blocks_and_clearances(events, match_id=match_id),
            *normalize_pressures_and_fouls(events, match_id=match_id),
        ]
        summary = build_defensive_summary(
            actions,
            normalize_aerial_duels(events, match_id=match_id),
            provider="statsbomb",
            match_id=str(match_id),
            events_sha256=events_sha256,
            available_periods=available_periods,
            periods=periods,
        )
        roster = build_defensive_roster(
            summary,
            lineups,
            scoped,
            lineups_sha256=lineups_sha256,
            participation_events_sha256=events_sha256,
        )
        result.players = {p.external_player_id: p for p in summary.players}
        result.teams = {t.external_team_id: t for t in summary.teams}
        result.population = {
            p.lineup.external_player_id: cast(
                Population,
                {
                    "observed_events": "observed",
                    "zero_with_presence": "presence_confirmed",
                    "unknown": "unknown",
                    "not_applicable": "not_applicable",
                }[p.counts_basis],
            )
            for p in roster.players
        }
    if needs_keepers:
        relations = normalize_event_relations(events, match_id=match_id)
        result.keepers = build_goalkeeper_statistics(
            normalize_goalkeeper_actions(events, match_id=match_id),
            normalize_shots(events, match_id=match_id),
            relations,
            provider="statsbomb",
            match_id=str(match_id),
            events_sha256=events_sha256,
            available_periods=(*available_periods, 5)
            if any(r.period == 5 for r in relations)
            else available_periods,
            periods=periods or available_periods,
        )
        snapshots = normalize_tactical_snapshots(events, match_id=match_id)
        selected = set(periods or available_periods)
        role_states: dict[str, list[str | None]] = {p.external_player_id: [] for p in players}
        for team_id in {p.external_team_id for p in players}:
            timeline = build_team_tactical_timeline(
                timed_snapshots=[
                    (
                        _elapsed(s.period, s.timestamp_ms, clock),
                        s,
                    )
                    for s in snapshots
                    if s.external_team_id == team_id
                ],
                players=[p for p in players if p.external_team_id == team_id],
                match_duration_ms=clock[-1].end_elapsed_ms,
            )
            for segment in timeline:
                if not any(
                    p.period in selected
                    and min(p.end_elapsed_ms, segment.end_elapsed_ms)
                    > max(p.start_elapsed_ms, segment.start_elapsed_ms)
                    for p in clock
                ):
                    continue
                for player in segment.players:
                    pos = player.last_recorded_position
                    role_states[player.external_player_id].append(
                        pos.position_name if pos else None
                    )
        listed = {p.external_player_id: p.external_team_id for p in scoped}
        if any(
            listed.get(p.external_player_id) != p.external_team_id for p in result.keepers.players
        ):
            raise ValueError("Goalkeeper action references unlisted player or wrong team")
        observed = {p.external_player_id for p in result.keepers.players}
        for p in scoped:
            states = role_states[p.external_player_id]
            if p.external_player_id in observed:
                if p.status != "observed_in_scope":
                    raise ValueError("Goalkeeper actions without participation in scope")
                result.roles[p.external_player_id] = "observed"
            elif p.status == "unknown":
                result.roles[p.external_player_id] = "unknown"
            elif p.status == "no_interval_in_scope":
                result.roles[p.external_player_id] = "not_applicable"
            elif "Goalkeeper" in states:
                result.roles[p.external_player_id] = "presence_confirmed"
            elif states and all(s is not None for s in states):
                result.roles[p.external_player_id] = "not_applicable"
            else:
                result.roles[p.external_player_id] = "unknown"
    return result


def defensive_value(
    identifier: str,
    summary: PlayerDefensiveSummary | TeamDefensiveSummary | None,
    population: Population,
) -> MetricValue:
    definition = get_metric(identifier)
    if population in {"unknown", "not_applicable"}:
        return _absent(identifier, "unknown" if population == "unknown" else "not_applicable")
    key = identifier.rsplit(".", 1)[-1]
    selection = dict(definition.selection)
    n = len(summary.external_event_ids) if summary else 0
    count: int | Decimal | None = 0
    numerator = denominator = None
    if definition.family == "defense":
        if identifier == "defense.unique_sources":
            count = n
        else:
            action = (
                next(
                    (a for a in summary.actions.actions if a.event_type == selection["event_type"]),
                    None,
                )
                if summary and summary.actions
                else None
            )
            n = action.event_count if action else 0
            count = (
                (
                    getattr(action.flags[selection["flag"]], key)
                    if "flag" in selection
                    else action.event_count
                )
                if action
                else 0
            )
    elif definition.family == "duels":
        duels = summary.duels if summary else None
        target = duels.tackles if ".tackles." in identifier and duels else duels
        count = getattr(target, key) if target else 0
        n = duels.total_duel_events if duels else 0
    else:
        aerials = summary.aerials if summary else None
        n = aerials.contested if aerials else 0
        count = getattr(aerials, key) if aerials else None if key == "win_pct" else 0
        if key == "win_pct":
            numerator, denominator = (aerials.won if aerials else 0), n
            if n == 0:
                return _absent(identifier, "not_applicable", "zero_denominator")
    return MetricValue(
        metric_id=identifier,
        unit=definition.unit,
        value=count,
        status="available",
        numerator=numerator,
        denominator=denominator,
        coverage=Coverage(
            source_complete=True, population=population, eligible_events=n, covered_events=n
        ),
    )


def _absent(
    identifier: str, population: Literal["unknown", "not_applicable"], reason: str | None = None
) -> MetricValue:
    return MetricValue(
        metric_id=identifier,
        unit=get_metric(identifier).unit,
        value=None,
        status=population,
        reasons=(reason or "population_" + population,),
        coverage=Coverage(
            source_complete=True, population=population, eligible_events=0, covered_events=0
        ),
    )


def goalkeeper_value(
    identifier: str,
    summary: PlayerGoalkeeperStatistics | TeamGoalkeeperStatistics | None,
    population: Population,
    result: GoalkeeperStatisticsResult,
) -> MetricValue:
    if population in {"unknown", "not_applicable"}:
        return _absent(
            identifier,
            "unknown" if population == "unknown" else "not_applicable",
            "goalkeeper_role_" + population,
        )
    definition = get_metric(identifier)
    scope = dict(definition.selection)["scope"]
    counts = (
        cast(GoalkeeperShotCounts, getattr(summary, scope))
        if summary
        else GoalkeeperShotCounts(
            pair_count=0,
            shot_goals_conceded=0,
            on_target_saves=0,
            off_target_saves=0,
            other_shots=0,
            review_pairs=0,
        )
    )
    reviews = []
    if counts.review_pairs:
        reviews.append("goalkeeper_pairs_require_review")
    if result.source_issues or result.unlinked_shot_event_ids:
        reviews.append("goalkeeper_source_link_issues_in_match")
    shot_action_types = {kind for kinds in EXPECTED_KEEPER_TYPES.values() for kind in kinds}
    if summary and any(
        action.goalkeeper_type in shot_action_types
        and set(action.external_event_ids).intersection(summary.unlinked_goalkeeper_event_ids)
        for action in summary.actions
    ):
        reviews.append("unlinked_goalkeeper_shot_action")
    return MetricValue(
        metric_id=identifier,
        unit=definition.unit,
        value=getattr(counts, identifier.rsplit(".", 1)[-1]),
        status="available",
        review_reasons=tuple(reviews),
        coverage=Coverage(
            source_complete=True,
            population=population,
            eligible_events=counts.pair_count,
            covered_events=counts.pair_count,
        ),
    )
