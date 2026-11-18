"""Read-only execution of integrated metric families using existing calculations."""

import hashlib
import json
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, TypedDict, cast
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from football_analytics.analytics.capabilities import EXECUTOR_FAMILIES
from football_analytics.analytics.catalogue import get_metric
from football_analytics.analytics.complementary_execution import (
    complementary_value,
    prepare_complementary,
)
from football_analytics.analytics.contracts import (
    Coverage,
    MetricQuery,
    MetricValue,
    QueryResult,
    ResultRow,
    SourceReference,
)
from football_analytics.analytics.defensive_execution import (
    defensive_value,
    goalkeeper_value,
    prepare_defense,
)
from football_analytics.analytics.definitions import Unit
from football_analytics.analytics.score_state_execution import (
    ScoreStateQuery,
    execute_score_state_query,
)
from football_analytics.ingestion.audited_roster import verify_stored_participation
from football_analytics.ingestion.match_participation import reconstruct_match_participation
from football_analytics.ingestion.offensive_roster import build_offensive_roster
from football_analytics.ingestion.offensive_summary import (
    PlayerOffensiveSummary,
    TeamOffensiveSummary,
    build_offensive_summary,
)
from football_analytics.ingestion.player_scope_participation import scope_player_participation
from football_analytics.ingestion.shot_xg_statistics import ShotXgTotals
from football_analytics.ingestion.stored_match_sources import load_latest_match_payload
from football_analytics.integrations.statsbomb.dribbles import normalize_dribbles
from football_analytics.integrations.statsbomb.lineups import normalize_lineups
from football_analytics.integrations.statsbomb.passes import normalize_passes
from football_analytics.integrations.statsbomb.period_clock import normalize_period_clock
from football_analytics.integrations.statsbomb.shots import normalize_shots
from football_analytics.integrations.statsbomb.starting_xi import normalize_starting_xi

Population = Literal["observed", "presence_confirmed", "unknown", "not_applicable"]


class ValueIdentity(TypedDict):
    metric_id: str
    unit: Unit


SUPPORTED_FAMILIES = EXECUTOR_FAMILIES


def _payload(connection: Connection, payload_id: UUID, expected_hash: str) -> object:
    row = connection.execute(
        text("""
        SELECT raw_body, payload, sha256 FROM raw.provider_payloads
        WHERE id=:id AND provider='statsbomb'
    """),
        {"id": payload_id},
    ).one()
    if (
        not isinstance(row.raw_body, str)
        or row.sha256 != expected_hash
        or hashlib.sha256(row.raw_body.encode("utf-8")).hexdigest() != expected_hash
    ):
        raise ValueError("Stored payload body/hash mismatch")
    decoded: object = json.loads(row.raw_body)
    if decoded != row.payload:
        raise ValueError("Stored JSON differs from hashed raw body")
    return decoded


def _sources(connection: Connection, mid: int) -> tuple[SourceReference, object, object]:
    kinds: tuple[Literal["events", "lineups"], ...] = ("events", "lineups")
    latest = {
        kind: load_latest_match_payload(connection, match_id=mid, kind=kind) for kind in kinds
    }
    calculation = connection.execute(
        text("""
        SELECT c.* FROM analytics.participation_calculations c
        JOIN core.external_id_mappings m ON m.match_id=c.match_id
        WHERE m.provider='statsbomb' AND m.entity_type='match' AND m.external_id=:mid
            AND c.metric_name='elapsed_on_field_ms' AND c.formula_version='1.0.0'
            AND c.events_sha256=:events AND c.lineups_sha256=:lineups
    """),
        {
            "mid": str(mid),
            "events": latest["events"]["sha256"],
            "lineups": latest["lineups"]["sha256"],
        },
    ).one_or_none()
    if calculation is None:
        raise ValueError(f"No participation calculation bound to current sources for match {mid}")
    # Use the exact sources bound to the calculation, not equivalent-but-unlinked payload IDs.
    events = _payload(connection, calculation.events_payload_id, calculation.events_sha256)
    lineups = _payload(connection, calculation.lineups_payload_id, calculation.lineups_sha256)
    for kind, payload_id in (
        ("events", calculation.events_payload_id),
        ("lineups", calculation.lineups_payload_id),
    ):
        endpoint: str = connection.execute(
            text("SELECT endpoint FROM raw.provider_payloads WHERE id=:id"), {"id": payload_id}
        ).scalar_one()
        if not endpoint.endswith(f"/{kind}/{mid}.json"):
            raise ValueError("Calculation source endpoint belongs to another match")
    periods = normalize_period_clock(events)
    source = SourceReference(
        match_id=mid,
        events_payload_id=calculation.events_payload_id,
        events_sha256=calculation.events_sha256,
        lineups_payload_id=calculation.lineups_payload_id,
        lineups_sha256=calculation.lineups_sha256,
        participation_calculation_id=calculation.id,
        available_periods=tuple(p.period for p in periods),
    )
    return source, events, lineups


def _value(
    identifier: str,
    summary: PlayerOffensiveSummary | TeamOffensiveSummary | None,
    population: Population,
    elapsed: int | None = None,
) -> MetricValue:
    definition = get_metric(identifier)
    base: ValueIdentity = {"metric_id": identifier, "unit": definition.unit}
    if definition.family == "participation":
        return MetricValue(
            **base,
            status="unknown" if elapsed is None else "available",
            value=elapsed,
            reasons=("participation_unknown",) if elapsed is None else (),
            coverage=Coverage(
                source_complete=True,
                population="unknown" if elapsed is None else "presence_confirmed",
                eligible_events=1,
                covered_events=0 if elapsed is None else 1,
            ),
        )
    if population in {"unknown", "not_applicable"}:
        return MetricValue(
            **base,
            status="unknown" if population == "unknown" else "not_applicable",
            value=None,
            reasons=("population_" + population,),
            coverage=Coverage(
                source_complete=True, population=population, eligible_events=0, covered_events=0
            ),
        )
    evidence: Population = "observed" if population == "observed" else "presence_confirmed"
    key = identifier.rsplit(".", 1)[-1]
    if definition.family in {"passes", "shots", "dribbles"}:
        stats = (
            (
                summary.passes
                if definition.family == "passes"
                else summary.dribbles
                if definition.family == "dribbles"
                else summary.shots
            )
            if summary
            else None
        )
        n = (
            cast(int, getattr(stats, "shots" if definition.family == "shots" else "attempted"))
            if stats
            else 0
        )
        coverage = Coverage(
            source_complete=True, population=evidence, eligible_events=n, covered_events=n
        )
        if key == "completion_pct":
            passes = (
                (summary.passes if definition.family == "passes" else summary.dribbles)
                if summary
                else None
            )
            completed = passes.completed if passes else 0
            if not n:
                return MetricValue(
                    **base,
                    status="not_applicable",
                    value=None,
                    numerator=0,
                    denominator=0,
                    reasons=("zero_denominator",),
                    coverage=coverage.model_copy(update={"population": "not_applicable"}),
                )
            return MetricValue(
                **base,
                status="available",
                value=passes.completion_pct if passes else None,
                numerator=completed,
                denominator=n,
                coverage=coverage,
            )
        member = "shots" if identifier == "shots.total" else key
        number: int | Decimal | None = cast(int, getattr(stats, member)) if stats is not None else 0
        return MetricValue(**base, status="available", value=number, coverage=coverage)
    xg = summary.xg if summary else None
    scope = dict(definition.selection).get("scope", "overall")
    totals = (
        cast(ShotXgTotals, getattr(xg, scope))
        if xg
        else ShotXgTotals(shots=0, goals=0, shots_with_xg=0, available_xg_sum=None)
    )
    missing = totals.missing_xg
    coverage = Coverage(
        source_complete=True,
        population=evidence,
        eligible_events=totals.shots,
        covered_events=totals.shots_with_xg,
    )
    if key in {"with_xg", "missing_xg"}:
        return MetricValue(
            **base,
            status="available",
            value=totals.shots_with_xg if key == "with_xg" else missing,
            coverage=coverage.model_copy(update={"covered_events": totals.shots}),
        )
    number = totals.xg_total if key == "xg" else totals.available_xg_sum
    if totals.shots == 0:
        number = Decimal(0)
    return MetricValue(
        **base,
        status="incomplete" if missing else "available",
        value=number,
        reasons=("missing_source_xg",) if missing else (),
        coverage=coverage,
    )


def _match_rows(
    connection: Connection, query: MetricQuery, mid: int
) -> tuple[SourceReference, list[ResultRow]]:
    source, events, lineup_payload = _sources(connection, mid)
    if query.periods and not set(query.periods).issubset(source.available_periods):
        raise ValueError(f"Requested periods unavailable for match {mid}")
    if source.participation_calculation_id is None or source.lineups_sha256 is None:
        raise ValueError("Participation provenance is required")
    players = reconstruct_match_participation(lineup_payload, events, mid)
    verify_stored_participation(connection, source.participation_calculation_id, players)
    clock = normalize_period_clock(events)
    scoped = scope_player_participation(
        players, clock, match_id=str(mid), selected_periods=query.periods
    )
    lineups = normalize_lineups(lineup_payload, mid, starting_xi=normalize_starting_xi(events))
    offense = build_offensive_summary(
        [
            *normalize_passes(events, match_id=mid),
            *normalize_shots(events, match_id=mid),
            *normalize_dribbles(events, match_id=mid),
        ],
        provider=query.provider,
        match_id=str(mid),
        events_sha256=source.events_sha256,
        available_periods=source.available_periods,
        periods=query.periods,
    )
    roster = build_offensive_roster(offense, lineups, scoped, lineups_sha256=source.lineups_sha256)
    families = {get_metric(m).family for m in query.metrics}
    defense = prepare_defense(
        events,
        match_id=mid,
        events_sha256=source.events_sha256,
        lineups_sha256=source.lineups_sha256,
        available_periods=source.available_periods,
        periods=query.periods,
        lineups=lineups,
        players=players,
        scoped=scoped,
        clock=clock,
        needs_defense=bool(families & {"defense", "duels", "aerial"}),
        needs_keepers="goalkeepers" in families,
    )

    complementary = prepare_complementary(
        events,
        match_id=mid,
        events_sha256=source.events_sha256,
        available_periods=source.available_periods,
        periods=query.periods,
        scoped=scoped,
        lineups=lineups,
        families=frozenset(families),
    )

    def selected_value(
        identifier: str,
        tid: str,
        pid: str | None,
        summary: PlayerOffensiveSummary | TeamOffensiveSummary | None,
        population: Population,
        elapsed: int | None = None,
    ) -> MetricValue:
        family = get_metric(identifier).family
        if family in complementary:
            return complementary_value(identifier, complementary[family], tid, pid)
        if family in {"defense", "duels", "aerial"}:
            ds = defense.players.get(pid) if pid is not None else defense.teams.get(tid)
            dp = defense.population[pid] if pid is not None else "presence_confirmed"
            return defensive_value(identifier, ds, dp)
        if family == "goalkeepers":
            keepers = defense.keepers
            if keepers is None:
                raise ValueError("Keeper context missing")
            if pid is not None:
                ks = next((p for p in keepers.players if p.external_player_id == pid), None)
                return goalkeeper_value(identifier, ks, defense.roles[pid], keepers)
            kt = next((t for t in keepers.teams if t.external_team_id == tid), None)
            team_roles = [
                defense.roles[p.external_player_id] for p in scoped if p.external_team_id == tid
            ]
            kp: Population = (
                "presence_confirmed"
                if any(r in {"observed", "presence_confirmed"} for r in team_roles)
                else "unknown"
            )
            return goalkeeper_value(identifier, kt, kp, keepers)
        return _value(identifier, summary, population, elapsed)

    rows = []
    if query.group_by == "player":
        for player in roster.players:
            tid, pid = player.lineup.external_team_id, player.lineup.external_player_id
            if (
                query.team_ids
                and tid not in query.team_ids
                or query.player_ids
                and pid not in query.player_ids
            ):
                continue
            basis = cast(
                Population,
                {
                    "observed_events": "observed",
                    "zero_with_presence": "presence_confirmed",
                    "unknown": "unknown",
                    "not_applicable": "not_applicable",
                }[player.counts_basis],
            )
            rows.append(
                ResultRow(
                    match_ids=(mid,),
                    team_id=tid,
                    player_id=pid,
                    values=tuple(
                        selected_value(
                            m,
                            tid,
                            pid,
                            player.observed_summary,
                            basis,
                            player.participation.elapsed_in_scope_ms,
                        )
                        for m in query.metrics
                    ),
                )
            )
    else:
        teams = {team.external_team_id: team for team in offense.teams}
        for tid in sorted({p.external_team_id for p in lineups}):
            if query.team_ids and tid not in query.team_ids:
                continue
            rows.append(
                ResultRow(
                    match_ids=(mid,),
                    team_id=tid,
                    values=tuple(
                        selected_value(m, tid, None, teams.get(tid), "presence_confirmed")
                        for m in query.metrics
                    ),
                )
            )
    return source, rows


def combine_values(values: list[MetricValue]) -> MetricValue:
    """Aggregate components, never average per-match percentages or hide unknowns."""
    if not values:
        raise ValueError("Cannot combine an empty metric selection")
    first = values[0]
    if len({v.metric_id for v in values}) != 1:
        raise ValueError("Cannot combine different metrics")
    base: ValueIdentity = {"metric_id": first.metric_id, "unit": first.unit}
    reviews = tuple(sorted({r for v in values for r in v.review_reasons}))
    applicable = [v for v in values if v.status != "not_applicable"]
    if not applicable:
        return MetricValue(
            **base,
            value=None,
            status="not_applicable",
            reasons=("no_applicable_match",),
            coverage=Coverage(
                source_complete=True,
                population="not_applicable",
                eligible_events=0,
                covered_events=0,
            ),
            review_reasons=reviews,
        )
    eligible = sum(v.coverage.eligible_events for v in applicable)
    covered = sum(v.coverage.covered_events for v in applicable)
    unknown = any(v.status == "unknown" for v in applicable)
    coverage = Coverage(
        source_complete=all(v.coverage.source_complete for v in applicable),
        population="unknown" if unknown else "presence_confirmed",
        eligible_events=eligible,
        covered_events=covered,
    )
    if unknown:
        return MetricValue(
            **base,
            status="unknown",
            value=None,
            coverage=coverage,
            reasons=("unknown_selected_match_value",),
            review_reasons=reviews,
        )
    incomplete = any(v.status == "incomplete" for v in applicable)
    definition = get_metric(first.metric_id)
    numerator = denominator = None
    if definition.aggregation == "ratio_of_sums":
        numerator = sum((v.numerator or 0 for v in applicable), 0)
        denominator = sum((v.denominator or 0 for v in applicable), 0)
        number: int | Decimal | None = (
            (Decimal(numerator) * 100 / Decimal(denominator)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if denominator > 0
            and all(v.numerator is not None and v.denominator is not None for v in applicable)
            else None
        )
    elif incomplete and first.metric_id.endswith(".xg"):
        number = None
    else:
        known = [v.value for v in applicable if v.value is not None]
        number = sum(known, Decimal(0) if first.unit == "expected_goals" else 0) if known else None
    return MetricValue(
        **base,
        status="incomplete" if incomplete else "available",
        value=number,
        numerator=numerator,
        denominator=denominator,
        coverage=coverage,
        reasons=("incomplete_selected_match_value",) if incomplete else (),
        review_reasons=reviews,
    )


def execute_query(connection: Connection, query: MetricQuery) -> QueryResult:
    """Caller manages a consistent read transaction; use run_query for the safe default."""
    if isinstance(query, ScoreStateQuery):
        return execute_score_state_query(connection, query)
    unsupported = [m for m in query.metrics if get_metric(m).family not in SUPPORTED_FAMILIES]
    if unsupported:
        raise ValueError(f"Metrics not connected to executor: {', '.join(unsupported)}")
    sources = []
    rows = []
    for mid in query.match_ids:
        source, selected = _match_rows(connection, query, mid)
        sources.append(source)
        rows.extend(selected)
    if not set(query.player_ids).issubset({r.player_id for r in rows}):
        raise ValueError("Requested player is not listed in selected matches/teams")
    if not set(query.team_ids).issubset({r.team_id for r in rows}):
        raise ValueError("Requested team has no rows in selected matches")
    if query.aggregation == "selected_matches":
        groups: dict[tuple[str, str | None], list[ResultRow]] = defaultdict(list)
        for row in rows:
            groups[row.team_id, row.player_id].append(row)
        rows = [
            ResultRow(
                match_ids=query.match_ids,
                team_id=tid,
                player_id=pid,
                values=tuple(
                    combine_values([next(v for v in r.values if v.metric_id == m) for r in group])
                    for m in query.metrics
                ),
            )
            for (tid, pid), group in sorted(groups.items(), key=repr)
        ]
    return QueryResult(query=query, sources=tuple(sources), rows=tuple(rows))


def run_query(engine: Engine, query: MetricQuery) -> QueryResult:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            return execute_query(connection, query)


def load_query_sources(
    connection: Connection, *, match_id: int
) -> tuple[SourceReference, object, object]:
    """Resolve and verify sources bound to the current participation calculation."""
    return _sources(connection, match_id)
