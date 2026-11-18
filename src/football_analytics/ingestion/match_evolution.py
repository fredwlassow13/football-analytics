"""Within-match score context and observed cumulative offensive metrics."""

from collections.abc import Sequence
from math import fsum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.creation_classification import (
    CreationClassificationResult,
    classify_creation_links,
)
from football_analytics.ingestion.creation_links import CreationAction
from football_analytics.ingestion.goal_score_evidence import (
    GoalScoreEvidence,
    build_goal_score_evidence,
)
from football_analytics.integrations.base import MatchEvent, MatchEventRelations, MatchShot
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary

type ScoreState = Literal["leading", "drawing", "trailing", "unknown"]


class EvolutionModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class EvolutionEvent(EvolutionModel):
    external_event_id: str
    source_index: int
    period: int
    timestamp_ms: int
    elapsed_ms: int
    external_team_id: str | None
    external_player_id: str | None
    event_type: str
    score_before: tuple[int, int] | None
    score_after: tuple[int, int] | None
    score_state: ScoreState
    scoring_team_id: str | None


class OffensiveTotals(EvolutionModel):
    shots: int
    shots_with_xg: int
    missing_xg: int
    xg_available: float
    xg: float | None
    created_shots: int
    recorded_shot_assists: int
    recorded_goal_assists: int


class EvolutionPoint(OffensiveTotals):
    external_event_id: str
    source_index: int
    period: int
    timestamp_ms: int
    elapsed_ms: int
    external_team_id: str


class ScoreStateTotals(OffensiveTotals):
    external_team_id: str
    score_state: ScoreState
    shot_event_ids: tuple[str, ...]
    creation_pass_event_ids: tuple[str, ...]


class MatchEvolution(EvolutionModel):
    policy_version: Literal["1.0.0"] = "1.0.0"
    provider: str
    external_match_id: str
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    team_ids: tuple[str, str]
    periods: tuple[PeriodBoundary, ...]
    goal_evidence: GoalScoreEvidence
    creation_evidence: CreationClassificationResult
    events: tuple[EvolutionEvent, ...]
    points: tuple[EvolutionPoint, ...]
    by_score_state: tuple[ScoreStateTotals, ...]
    creation_review_pass_ids: tuple[str, ...]
    excluded_shootout_event_ids: tuple[str, ...]
    issues: tuple[str, ...]


def build_match_evolution(
    events: Sequence[MatchEvent],
    shots: Sequence[MatchShot],
    relations: Sequence[MatchEventRelations],
    creation: Sequence[CreationAction],
    periods: Sequence[PeriodBoundary],
    *,
    provider: str,
    match_id: str,
    team_ids: tuple[str, str],
    events_sha256: str,
) -> MatchEvolution:
    """Use complete match sources; score-state attribution precedes each event."""
    ordered = sorted(events, key=lambda e: e.source_index)
    catalogue = {e.external_event_id: e for e in ordered}
    if len(catalogue) != len(ordered) or len({e.source_index for e in ordered}) != len(ordered):
        raise ValueError("Duplicate evolution event")
    boundaries = {p.period: p for p in periods}
    if set(boundaries) not in ({1, 2}, {1, 2, 3, 4}) or len(boundaries) != len(periods):
        raise ValueError("Complete playing periods required")
    end = 0
    for p in sorted(periods, key=lambda p: p.period):
        if (
            p.duration_ms <= 0
            or p.start_elapsed_ms != end
            or p.end_elapsed_ms != end + p.duration_ms
        ):
            raise ValueError("Invalid period boundaries")
        end = p.end_elapsed_ms
    for e in ordered:
        if (e.provider, e.external_match_id) != (provider, match_id):
            raise ValueError("Evolution event belongs to another source")
        if e.external_team_id is not None and e.external_team_id not in team_ids:
            raise ValueError("Unknown evolution team")
        if e.event_type in {"Pass", "Shot"} and e.external_team_id is None:
            raise ValueError("Offensive action requires a team")
        if e.period != 5 and (
            e.period not in boundaries or e.timestamp_ms > boundaries[e.period].duration_ms
        ):
            raise ValueError("Evolution event outside period clock")
    for actions in (creation, shots):
        seen: set[str] = set()
        for a in actions:
            source = catalogue.get(a.external_event_id)
            if (
                a.external_event_id in seen
                or source is None
                or any(getattr(a, f) != getattr(source, f) for f in MatchEvent.model_fields)
            ):
                raise ValueError("Evolution action coverage or header differs")
            seen.add(a.external_event_id)
    expected_creation = {e.external_event_id for e in events if e.event_type in {"Pass", "Shot"}}
    if {a.external_event_id for a in creation} != expected_creation:
        raise ValueError("Complete creation coverage required")
    # Shot attributes must agree across both normalized views.
    creation_by_id = {a.external_event_id: a for a in creation}
    if any(
        any(
            getattr(s, f) != getattr(creation_by_id[s.external_event_id], f)
            for f in MatchShot.model_fields
        )
        for s in shots
    ):
        raise ValueError("Creation and shot attributes differ")
    evidence = build_goal_score_evidence(
        events,
        shots,
        relations,
        provider=provider,
        match_id=match_id,
        team_ids=team_ids,
        events_sha256=events_sha256,
    )
    classified = classify_creation_links(
        creation,
        events,
        provider=provider,
        match_id=match_id,
        events_sha256=events_sha256,
    )
    usable = {
        c.external_pass_event_id: c
        for c in classified.classifications
        if c.status == "usable" and c.pass_period != 5
    }
    scoring = {key: catalogue[key].external_team_id for key in evidence.shot_goal_event_ids}
    for pair in evidence.own_goal_links.pairs:
        if pair.status == "usable":
            key = min(
                (pair.for_event_id, pair.against_event_id),
                key=lambda key: catalogue[key].source_index,
            )
            scoring[key] = catalogue[pair.for_event_id].external_team_id
    shot_map = {s.external_event_id: s for s in shots}
    score = [0, 0]
    rows: list[EvolutionEvent] = []
    issues = set(evidence.issues)
    previous = (0, 0)
    for e in ordered:
        if e.period == 5:
            continue
        clock = (e.period, e.timestamp_ms)
        if clock < previous:
            issues.add("source_clock_not_monotonic")
        previous = clock
        known = evidence.status == "resolved"
        before = (score[0], score[1]) if known else None
        state: ScoreState = "unknown"
        if known and e.external_team_id is not None:
            own = team_ids.index(e.external_team_id)
            difference = score[own] - score[1 - own]
            state = "leading" if difference > 0 else "trailing" if difference < 0 else "drawing"
        scorer = scoring.get(e.external_event_id)
        if scorer is not None:
            score[team_ids.index(scorer)] += 1
        rows.append(
            EvolutionEvent(
                external_event_id=e.external_event_id,
                source_index=e.source_index,
                period=e.period,
                timestamp_ms=e.timestamp_ms,
                elapsed_ms=boundaries[e.period].start_elapsed_ms + e.timestamp_ms,
                external_team_id=e.external_team_id,
                external_player_id=e.external_player_id,
                event_type=e.event_type,
                score_before=before,
                score_after=(score[0], score[1]) if known else None,
                score_state=state,
                scoring_team_id=scorer if known else None,
            )
        )

    def totals(ids: Sequence[str]) -> OffensiveTotals:
        selected_shots = [shot_map[key] for key in ids if key in shot_map]
        linked = [usable[key] for key in ids if key in usable]
        values = [s.statsbomb_xg for s in selected_shots if s.statsbomb_xg is not None]
        missing = len(selected_shots) - len(values)
        return OffensiveTotals(
            shots=len(selected_shots),
            shots_with_xg=len(values),
            missing_xg=missing,
            xg_available=fsum(values),
            xg=None if missing else fsum(values),
            created_shots=len(linked),
            recorded_shot_assists=sum(c.shot_assist is True for c in linked),
            recorded_goal_assists=sum(c.goal_assist is True for c in linked),
        )

    accumulated: dict[str, list[str]] = {team: [] for team in team_ids}
    points: list[EvolutionPoint] = []
    for row in rows:
        team = row.external_team_id
        if team is None or row.external_event_id not in (shot_map.keys() | usable.keys()):
            continue
        accumulated[team].append(row.external_event_id)
        points.append(
            EvolutionPoint(
                **totals(accumulated[team]).model_dump(),
                external_event_id=row.external_event_id,
                source_index=row.source_index,
                period=row.period,
                timestamp_ms=row.timestamp_ms,
                elapsed_ms=row.elapsed_ms,
                external_team_id=team,
            )
        )
    groups = []
    for team in team_ids:
        for state in ("leading", "drawing", "trailing", "unknown"):
            ids = [
                r.external_event_id
                for r in rows
                if r.external_team_id == team and r.score_state == state
            ]
            groups.append(
                ScoreStateTotals(
                    **totals(ids).model_dump(),
                    external_team_id=team,
                    score_state=state,
                    shot_event_ids=tuple(key for key in ids if key in shot_map),
                    creation_pass_event_ids=tuple(key for key in ids if key in usable),
                )
            )
    return MatchEvolution(
        provider=provider,
        external_match_id=match_id,
        events_sha256=events_sha256,
        team_ids=team_ids,
        periods=tuple(sorted(periods, key=lambda p: p.period)),
        goal_evidence=evidence,
        creation_evidence=classified,
        events=tuple(rows),
        points=tuple(points),
        by_score_state=tuple(groups),
        creation_review_pass_ids=tuple(
            sorted(
                {
                    c.external_pass_event_id
                    for c in classified.classifications
                    if c.status == "review" and c.pass_period != 5
                }
            )
        ),
        excluded_shootout_event_ids=tuple(e.external_event_id for e in ordered if e.period == 5),
        issues=tuple(sorted(issues)),
    )


def select_score_state_events[T: MatchEvent](
    actions: Sequence[T],
    evolution: MatchEvolution,
    *,
    team_id: str,
    state: ScoreState,
    periods: tuple[int, ...] | None = None,
) -> tuple[T, ...]:
    """Select either team's actions from a complete validated timeline."""
    available = {p.period for p in evolution.periods}
    selected = available if periods is None else set(periods)
    if (
        team_id not in evolution.team_ids
        or state not in {"leading", "drawing", "trailing", "unknown"}
        or not selected
        or not selected <= available
        or periods is not None
        and (len(selected) != len(periods) or any(type(p) is not int for p in periods))
    ):
        raise ValueError("Invalid score-state selection")
    by_id = {e.external_event_id: e for e in evolution.events}
    excluded = set(evolution.excluded_shootout_event_ids)
    seen: set[str] = set()
    result = []
    for a in sorted(actions, key=lambda a: a.source_index):
        if (a.provider, a.external_match_id) != (evolution.provider, evolution.external_match_id):
            raise ValueError("Action belongs to another source")
        if a.external_event_id in seen:
            raise ValueError("Duplicate score-state action")
        seen.add(a.external_event_id)
        if a.period == 5 and a.external_event_id in excluded:
            continue
        row = by_id.get(a.external_event_id)
        if row is None or any(
            getattr(a, f) != getattr(row, f)
            for f in (
                "source_index",
                "period",
                "timestamp_ms",
                "event_type",
                "external_team_id",
                "external_player_id",
            )
        ):
            raise ValueError("Action differs from evolution source")
        if row.period not in selected or a.external_team_id != team_id:
            continue
        if row.score_state == state:
            result.append(a)
    return tuple(result)
