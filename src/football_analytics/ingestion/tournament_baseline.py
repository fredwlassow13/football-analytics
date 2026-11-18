"""Reusable observed team baseline. No per-90 or time-based possession claims."""

import argparse
from collections import Counter, defaultdict
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Connection, create_engine, text

from football_analytics.ingestion.defensive_summary import build_defensive_summary
from football_analytics.ingestion.match_score_verification import verify_match_score
from football_analytics.ingestion.offensive_summary import build_offensive_summary
from football_analytics.integrations.statsbomb.aerial_duels import normalize_aerial_duels
from football_analytics.integrations.statsbomb.ball_recoveries import normalize_ball_recoveries
from football_analytics.integrations.statsbomb.blocks_and_clearances import (
    normalize_blocks_and_clearances,
)
from football_analytics.integrations.statsbomb.dribbles import normalize_dribbles
from football_analytics.integrations.statsbomb.duels import normalize_duels
from football_analytics.integrations.statsbomb.interceptions import normalize_interceptions
from football_analytics.integrations.statsbomb.passes import normalize_passes
from football_analytics.integrations.statsbomb.period_clock import normalize_period_clock
from football_analytics.integrations.statsbomb.pressures_and_fouls import (
    normalize_pressures_and_fouls,
)
from football_analytics.integrations.statsbomb.shots import normalize_shots

COUNT_FIELDS = (
    "goals_for",
    "goals_against",
    "shot_goals",
    "own_goals_for",
    "passes",
    "passes_complete",
    "passes_unknown",
    "injury_clearances",
    "shots",
    "shots_on_target",
    "shots_against",
    "shots_with_xg",
    "penalty_shots",
    "unclassified_shot_type_count",
    "dribbles",
    "dribbles_complete",
    "defensive_unique_sources",
    "defensive_action_sources",
    "aerial_results",
    "aerial_won",
    "aerial_lost",
)
XG_FIELDS = ("xg", "xg_against", "penalty_xg", "non_penalty_xg")


class BaselineTotals(BaseModel):
    goals_for: int = Field(ge=0)
    goals_against: int = Field(ge=0)
    shot_goals: int = Field(ge=0)
    own_goals_for: int = Field(ge=0)
    passes: int = Field(ge=0)
    passes_complete: int = Field(ge=0)
    passes_unknown: int = Field(ge=0)
    injury_clearances: int = Field(ge=0)
    shots: int = Field(ge=0)
    shots_on_target: int = Field(ge=0)
    shots_against: int = Field(ge=0)
    shots_with_xg: int = Field(ge=0)
    penalty_shots: int = Field(ge=0)
    unclassified_shot_type_count: int = Field(ge=0)
    dribbles: int = Field(ge=0)
    dribbles_complete: int = Field(ge=0)
    defensive_unique_sources: int = Field(ge=0)
    defensive_action_sources: int = Field(ge=0)
    aerial_results: int = Field(ge=0)
    aerial_won: int = Field(ge=0)
    aerial_lost: int = Field(ge=0)
    xg: Decimal = Field(ge=0, allow_inf_nan=False)
    xg_against: Decimal = Field(ge=0, allow_inf_nan=False)
    penalty_xg: Decimal = Field(ge=0, allow_inf_nan=False)
    non_penalty_xg: Decimal = Field(ge=0, allow_inf_nan=False)
    defensive_type_counts: dict[str, int]


class TeamMatchBaseline(BaselineTotals):
    match_id: int
    team_id: str
    team: str
    opponent_id: str
    opponent: str
    periods: tuple[int, ...]
    events_payload_id: str
    events_sha256: str
    matches_payload_id: str
    matches_sha256: str


class TeamBaseline(BaselineTotals):
    team_id: str
    team: str
    matches: int
    extra_time_matches: int
    xg_per_match: Decimal
    xg_against_per_match: Decimal
    xg_difference_per_match: Decimal
    shots_per_match: Decimal
    goals_per_match: Decimal
    pass_completion_pct: Decimal | None


class TournamentBaseline(BaseModel):
    policy_version: str = "1.0.0"
    scope: str = "observed_match_and_team_baseline"
    provider: str = "statsbomb"
    requested_match_ids: tuple[int, ...]
    matches: int
    teams: tuple[TeamBaseline, ...]
    team_matches: tuple[TeamMatchBaseline, ...]
    totals: BaselineTotals
    limitations: tuple[str, ...] = (
        "Per-match averages include extra time; they are not per-90 rates.",
        "No opponent-strength adjustment or causal interpretation.",
        "Defensive records and aerial results do not equal unique successful recoveries.",
        "Shootout events excluded from these totals.",
        "Own goals follow StatsBomb attribution and the reconciled provider score.",
        "This report does not calculate time-based possession.",
        "Only complete xG coverage is accepted by this baseline policy.",
    )


def _totals(rows: Sequence[BaselineTotals]) -> dict[str, Any]:
    result = {key: sum(getattr(r, key) for r in rows) for key in COUNT_FIELDS}
    result.update({key: sum((getattr(r, key) for r in rows), Decimal(0)) for key in XG_FIELDS})
    counts: Counter[str] = Counter()
    for row in rows:
        counts.update(row.defensive_type_counts)
    result["defensive_type_counts"] = dict(counts)
    return result


def aggregate_team_baselines(rows: Sequence[TeamMatchBaseline]) -> tuple[TeamBaseline, ...]:
    if len({(r.match_id, r.team_id) for r in rows}) != len(rows):
        raise ValueError("Duplicate team-match row")
    groups: dict[str, list[TeamMatchBaseline]] = defaultdict(list)
    for row in rows:
        groups[row.team_id].append(row)
    teams = []
    for tid, group in groups.items():
        totals = BaselineTotals.model_validate(_totals(group))
        n = len(group)
        teams.append(
            TeamBaseline(
                **totals.model_dump(),
                team_id=tid,
                team=group[0].team,
                matches=n,
                extra_time_matches=sum(3 in r.periods for r in group),
                xg_per_match=totals.xg / n,
                xg_against_per_match=totals.xg_against / n,
                xg_difference_per_match=(totals.xg - totals.xg_against) / n,
                shots_per_match=Decimal(totals.shots) / n,
                goals_per_match=Decimal(totals.goals_for) / n,
                pass_completion_pct=(
                    Decimal(totals.passes_complete) * 100 / totals.passes if totals.passes else None
                ),
            )
        )
    return tuple(sorted(teams, key=lambda row: (row.team, row.team_id)))


def build_match_baseline(connection: Connection, *, match_id: int) -> list[TeamMatchBaseline]:
    # The verifier resolves, checks and identifies the exact event and match sources.
    score = verify_match_score(connection, match_id=match_id)
    if score["status"] != "matched":
        raise ValueError(f"Score requires review for match {match_id}")
    source = connection.execute(
        text("SELECT payload,sha256 FROM raw.provider_payloads WHERE id=:id"),
        {"id": score["events_payload_id"]},
    ).one()
    if source.sha256 != score["events_sha256"]:
        raise ValueError("Event source differs from verified score")
    payload = source.payload
    periods = tuple(c.period for c in normalize_period_clock(payload))
    passes = normalize_passes(payload, match_id=match_id)
    shots = normalize_shots(payload, match_id=match_id)
    dribbles = normalize_dribbles(payload, match_id=match_id)
    actions = [
        *normalize_duels(payload, match_id=match_id),
        *normalize_interceptions(payload, match_id=match_id),
        *normalize_ball_recoveries(payload, match_id=match_id),
        *normalize_blocks_and_clearances(payload, match_id=match_id),
        *normalize_pressures_and_fouls(payload, match_id=match_id),
    ]
    offense = build_offensive_summary(
        [*passes, *shots, *dribbles],
        provider="statsbomb",
        match_id=str(match_id),
        events_sha256=source.sha256,
        available_periods=periods,
    )
    defense = build_defensive_summary(
        actions,
        normalize_aerial_duels(payload, match_id=match_id),
        provider="statsbomb",
        match_id=str(match_id),
        events_sha256=source.sha256,
        available_periods=periods,
    )
    off = {t.external_team_id: t for t in offense.teams}
    deff = {t.external_team_id: t for t in defense.teams}
    # JSON fields are intentionally validated at the boundaries by the existing verifier.
    checked: Any = score
    goals = {t["external_team_id"]: t for t in checked["teams"]}
    own = {
        t["external_team_id"]: t["resolved_own_goals"]
        for t in checked["reconciliation"]["evidence"]["teams"]
    }
    if len(goals) != 2:
        raise ValueError("Expected exactly two verified teams")
    names: dict[str, str] = dict(
        connection.execute(
            text("""
        SELECT m.external_id, t.name FROM core.external_id_mappings m
        JOIN core.teams t ON t.id=m.team_id
        WHERE m.provider='statsbomb' AND m.entity_type='team'
            AND m.external_id IN (:a,:b)
    """),
            {"a": next(iter(goals)), "b": list(goals)[1]},
        ).all()
    )
    if len(goals) != 2 or set(goals) != set(off) or len(names) != 2:
        raise ValueError("Baseline team population differs from verified match")
    result: list[TeamMatchBaseline] = []
    for tid, goal in goals.items():
        opponent_id = next(t for t in goals if t != tid)
        os = off[tid]
        ds = deff.get(tid)
        ps, ss, dr, xg = os.passes, os.shots, os.dribbles, os.xg
        # Zero shots is complete coverage; a shot with missing xG is not zero xG.
        if xg is not None and xg.overall.missing_xg:
            raise ValueError(f"Incomplete xG coverage for match {match_id}, team {tid}")
        opponent_xg = off[opponent_id].xg
        opponent_shots = off[opponent_id].shots
        raw = Counter(
            e["type"]["name"]
            for e in payload
            if e["period"] in periods and str(e.get("team", {}).get("id")) == tid
        )
        values = dict(
            match_id=match_id,
            team_id=tid,
            team=names[tid],
            opponent_id=opponent_id,
            opponent=names[opponent_id],
            periods=periods,
            events_payload_id=str(score["events_payload_id"]),
            events_sha256=str(score["events_sha256"]),
            matches_payload_id=str(score["matches_payload_id"]),
            matches_sha256=str(score["matches_sha256"]),
            goals_for=goal["recorded_goals"],
            goals_against=goals[opponent_id]["recorded_goals"],
            shot_goals=ss.goals if ss else 0,
            own_goals_for=own[tid],
            passes=ps.attempted if ps else 0,
            passes_complete=ps.completed if ps else 0,
            passes_unknown=ps.unknown if ps else 0,
            injury_clearances=ps.injury_clearance if ps else 0,
            shots=ss.shots if ss else 0,
            shots_on_target=ss.shots_on_target if ss else 0,
            shots_against=opponent_shots.shots if opponent_shots else 0,
            shots_with_xg=xg.overall.shots_with_xg if xg else 0,
            xg=(xg.overall.available_xg_sum or Decimal(0)) if xg else Decimal(0),
            xg_against=(opponent_xg.overall.available_xg_sum or Decimal(0))
            if opponent_xg
            else Decimal(0),
            penalty_shots=xg.penalties.shots if xg else 0,
            penalty_xg=(xg.penalties.available_xg_sum or Decimal(0)) if xg else Decimal(0),
            non_penalty_xg=(xg.non_penalties.available_xg_sum or Decimal(0)) if xg else Decimal(0),
            unclassified_shot_type_count=xg.unclassified_type.shots if xg else 0,
            dribbles=dr.attempted if dr else 0,
            dribbles_complete=dr.completed if dr else 0,
            defensive_unique_sources=len(ds.external_event_ids) if ds else 0,
            defensive_action_sources=ds.actions.event_count if ds and ds.actions else 0,
            aerial_results=ds.aerials.contested if ds and ds.aerials else 0,
            aerial_won=ds.aerials.won if ds and ds.aerials else 0,
            aerial_lost=ds.aerials.lost if ds and ds.aerials else 0,
            defensive_type_counts={a.event_type: a.event_count for a in ds.actions.actions}
            if ds and ds.actions
            else {},
        )
        row = TeamMatchBaseline.model_validate(values)
        if (row.shots, row.passes, row.dribbles) != (raw["Shot"], raw["Pass"], raw["Dribble"]):
            raise ValueError("Offensive counts differ from raw source")
        if row.shot_goals + row.own_goals_for != row.goals_for:
            raise ValueError("Goals do not recompose score")
        if any(raw[k] != v for k, v in row.defensive_type_counts.items()):
            raise ValueError("Defensive counts differ from raw source")
        result.append(row)
    if sum(len(p.external_event_ids) for p in offense.players) != len(offense.external_event_ids):
        raise ValueError("Offensive player totals differ")
    if sum(len(p.external_event_ids) for p in defense.players) != len(defense.external_event_ids):
        raise ValueError("Defensive player totals differ")
    return result


def build_tournament_baseline(
    connection: Connection, match_ids: Sequence[int]
) -> TournamentBaseline:
    requested = tuple(match_ids)
    if not requested or any(type(mid) is not int or mid <= 0 for mid in requested):
        raise ValueError("Provide positive match IDs")
    if len(requested) != len(set(requested)):
        raise ValueError("Duplicate match IDs")
    rows = [row for mid in requested for row in build_match_baseline(connection, match_id=mid)]
    totals = BaselineTotals.model_validate(_totals(rows))
    if totals.goals_for != totals.goals_against or totals.xg != totals.xg_against:
        raise ValueError("Tournament for/against totals differ")
    if totals.shots != totals.shots_with_xg:
        raise ValueError("Incomplete tournament xG coverage")
    return TournamentBaseline(
        requested_match_ids=requested,
        matches=len(requested),
        teams=aggregate_team_baselines(rows),
        team_matches=tuple(rows),
        totals=totals,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--match-ids", nargs="+", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    engine = create_engine(args.database_url)
    try:
        # One consistent read snapshot across score checks and subsequent calculations.
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                report = build_tournament_baseline(connection, args.match_ids)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        print(f"Matches: {report.matches}; teams: {len(report.teams)}; output: {args.output}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
