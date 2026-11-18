"""Team scalar metrics filtered by the score before each action."""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal, Self, cast

from pydantic import model_validator
from sqlalchemy import Connection

from football_analytics.analytics.catalogue import get_metric
from football_analytics.analytics.complementary_execution import FamilyContext, complementary_value
from football_analytics.analytics.contracts import (
    Contract,
    Coverage,
    MetricQuery,
    MetricValue,
    QueryResult,
    ResultRow,
    SourceReference,
)
from football_analytics.analytics.defensive_execution import defensive_value
from football_analytics.analytics.score_state_complementary import (
    COMPLEMENTARY_SCORE_FAMILIES,
    ScoreStateComplementaryEvidence,
    prepare_score_state_complementary,
)
from football_analytics.analytics.score_state_defense import (
    DEFENSIVE_SCORE_FAMILIES,
    ScoreStateDefensiveEvidence,
    prepare_score_state_defense,
)
from football_analytics.analytics.score_state_goalkeepers import (
    ScoreStateGoalkeeperEvidence,
    TeamKeeperPopulation,
    goalkeeper_team_population,
    prepare_score_state_goalkeepers,
)
from football_analytics.ingestion.audited_roster import verify_stored_participation
from football_analytics.ingestion.creation_classification import CreationClassification
from football_analytics.ingestion.creation_links import CreationReferenceIssue
from football_analytics.ingestion.creation_statistics import CreationStatistics, _summarize_passes
from football_analytics.ingestion.match_evolution import MatchEvolution, build_match_evolution
from football_analytics.ingestion.match_participation import reconstruct_match_participation
from football_analytics.ingestion.offensive_summary import (
    TeamOffensiveSummary,
    build_offensive_summary,
)
from football_analytics.integrations.base import (
    MatchDribble,
    MatchPass,
    MatchPassCreation,
    MatchShot,
)
from football_analytics.integrations.statsbomb.creation import normalize_creation_events
from football_analytics.integrations.statsbomb.dribbles import normalize_dribbles
from football_analytics.integrations.statsbomb.event_relations import normalize_event_relations
from football_analytics.integrations.statsbomb.events import normalize_events
from football_analytics.integrations.statsbomb.lineups import normalize_lineups
from football_analytics.integrations.statsbomb.passes import normalize_passes
from football_analytics.integrations.statsbomb.period_clock import normalize_period_clock
from football_analytics.integrations.statsbomb.shots import normalize_shots
from football_analytics.integrations.statsbomb.starting_xi import normalize_starting_xi

SCORE_STATE_FAMILIES = (
    frozenset({"passes", "shots", "xg", "dribbles", "creation", "goalkeepers"})
    | DEFENSIVE_SCORE_FAMILIES
    | COMPLEMENTARY_SCORE_FAMILIES
)


class ScoreStateQuery(MetricQuery):
    """Explicit extension; ordinary MetricQuery continues to reject this filter."""

    score_state_contract_version: Literal["1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"] = "1.0.0"
    score_state: Literal["leading", "drawing", "trailing"]

    @model_validator(mode="after")
    def check_score_scope(self) -> Self:
        if self.group_by != "team":
            raise ValueError("Score-state queries currently support team grouping only")
        if any(get_metric(m).family not in SCORE_STATE_FAMILIES for m in self.metrics):
            raise ValueError("Unsupported metric family for score-state filtering")
        if any(get_metric(m).family == "creation" for m in self.metrics) and (
            self.score_state_contract_version not in {"1.1.0", "1.2.0", "1.3.0", "1.4.0"}
        ):
            raise ValueError("Creation by score requires score_state_contract_version=1.1.0")
        if any(get_metric(m).family in DEFENSIVE_SCORE_FAMILIES for m in self.metrics) and (
            self.score_state_contract_version not in {"1.2.0", "1.3.0", "1.4.0"}
        ):
            raise ValueError(
                "Defensive score filtering requires score_state_contract_version=1.2.0"
            )
        if any(get_metric(m).family == "goalkeepers" for m in self.metrics) and (
            self.score_state_contract_version not in {"1.3.0", "1.4.0"}
        ):
            raise ValueError("Keeper score filtering requires score_state_contract_version=1.3.0")
        if (
            any(get_metric(m).family in COMPLEMENTARY_SCORE_FAMILIES for m in self.metrics)
            and self.score_state_contract_version != "1.4.0"
        ):
            raise ValueError(
                "Progression/control by score requires score_state_contract_version=1.4.0"
            )
        return self


class ScoreStateCreationEvidence(Contract):
    scope_policy_version: Literal["1.0.0"] = "1.0.0"
    scope_basis: Literal["pass_period_and_pre_action_score"] = "pass_period_and_pre_action_score"
    classification_policy_version: Literal["1.0.0"] = "1.0.0"
    link_policy_version: Literal["1.0.0"] = "1.0.0"
    team_id: str
    summary: CreationStatistics
    pairs: tuple[CreationClassification, ...]
    match_reference_issues: tuple[CreationReferenceIssue, ...]

    @model_validator(mode="after")
    def check_creation_alignment(self) -> Self:
        ids = self.summary.pass_event_ids
        if len(set(ids)) != len(ids) or self.summary.counts.pass_count != len(ids):
            raise ValueError("Creation pass evidence differs from population count")
        if any(p.external_pass_event_id not in ids or p.pass_period == 5 for p in self.pairs):
            raise ValueError("Creation pair is outside selected pass evidence")
        keys = {(p.external_pass_event_id, p.external_shot_event_id) for p in self.pairs}
        usable = sum(p.status == "usable" for p in self.pairs)
        counts = self.summary.counts
        if (
            len(keys) != len(self.pairs)
            or counts.candidate_pairs != len(self.pairs)
            or counts.usable_pairs != usable
            or counts.review_pairs != len(self.pairs) - usable
        ):
            raise ValueError("Creation pair counts differ from preserved evidence")
        return self


class ScoreStateEvidence(Contract):
    match_id: int
    events_sha256: str
    policy_version: Literal["1.0.0"] = "1.0.0"
    team_ids: tuple[str, str]
    goal_status: Literal["resolved", "review"]
    final_score: tuple[int, int] | None
    issues: tuple[str, ...]
    creation_scope: tuple[ScoreStateCreationEvidence, ...] = ()
    defensive_scope: tuple[ScoreStateDefensiveEvidence, ...] = ()
    goalkeeper_scope: tuple[ScoreStateGoalkeeperEvidence, ...] = ()
    complementary_scope: tuple[ScoreStateComplementaryEvidence, ...] = ()


class ScoreStateResult(QueryResult):
    query: ScoreStateQuery
    score_state_contract_version: Literal["1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"] = "1.0.0"
    score_evidence: tuple[ScoreStateEvidence, ...]

    @model_validator(mode="after")
    def check_score_evidence(self) -> Self:
        if self.score_state_contract_version != self.query.score_state_contract_version:
            raise ValueError("Score result extension version differs from query")
        requires_creation = any(get_metric(m).family == "creation" for m in self.query.metrics)
        requires_defense = any(
            get_metric(m).family in DEFENSIVE_SCORE_FAMILIES for m in self.query.metrics
        )
        requires_keepers = any(get_metric(m).family == "goalkeepers" for m in self.query.metrics)
        references = {s.match_id: s for s in self.sources}
        evidence = {e.match_id: e for e in self.score_evidence}
        if len(evidence) != len(self.score_evidence) or set(evidence) != set(references):
            raise ValueError("Score evidence must cover exactly the result sources")
        for mid, entry in evidence.items():
            if entry.events_sha256 != references[mid].events_sha256:
                raise ValueError("Score evidence hash differs from source")
            if (entry.goal_status == "resolved") != (entry.final_score is not None):
                raise ValueError("Final score availability differs from goal evidence status")
            if requires_creation and entry.goal_status == "resolved":
                scope_teams = [scope.team_id for scope in entry.creation_scope]
                if len(scope_teams) != 2 or set(scope_teams) != set(entry.team_ids):
                    raise ValueError("Creation scope must cover both source teams")
            if requires_defense and entry.goal_status == "resolved":
                scope_teams = [scope.team_id for scope in entry.defensive_scope]
                if len(scope_teams) != 2 or set(scope_teams) != set(entry.team_ids):
                    raise ValueError("Defensive scope must cover both source teams")
            if requires_keepers and entry.goal_status == "resolved":
                scope_teams = [scope.team_id for scope in entry.goalkeeper_scope]
                if len(scope_teams) != 2 or set(scope_teams) != set(entry.team_ids):
                    raise ValueError("Keeper scope must cover both source teams")
            for family in COMPLEMENTARY_SCORE_FAMILIES:
                if (
                    any(get_metric(m).family == family for m in self.query.metrics)
                    and entry.goal_status == "resolved"
                ):
                    teams = [s.team_id for s in entry.complementary_scope if s.family == family]
                    if len(teams) != 2 or set(teams) != set(entry.team_ids):
                        raise ValueError("Complementary scope must cover both source teams")
        for row in self.rows:
            if any(
                evidence[mid].goal_status == "review" and row.team_id in evidence[mid].team_ids
                for mid in row.match_ids
            ):
                if any(v.status != "unknown" for v in row.values):
                    raise ValueError("Unresolved score must keep filtered values unknown")
        return self


@dataclass
class ScoreStateContext:
    evolution: MatchEvolution
    teams: dict[str, TeamOffensiveSummary]
    creation_scopes: dict[str, ScoreStateCreationEvidence] = field(default_factory=dict)
    defensive_scopes: dict[str, ScoreStateDefensiveEvidence] = field(default_factory=dict)
    goalkeeper_scopes: dict[str, ScoreStateGoalkeeperEvidence] = field(default_factory=dict)
    complementary: dict[str, FamilyContext] = field(default_factory=dict)
    complementary_scopes: tuple[ScoreStateComplementaryEvidence, ...] = ()


def prepare_score_state(
    payload: object,
    *,
    query: ScoreStateQuery,
    match_id: int,
    team_ids: tuple[str, str],
    events_sha256: str,
    keeper_population: dict[str, TeamKeeperPopulation] | None = None,
) -> ScoreStateContext:
    """Reconstruct the complete score first, then filter actions and periods."""
    if match_id not in query.match_ids:
        raise ValueError("Match is outside query selection")
    clock = normalize_period_clock(payload)
    available = tuple(p.period for p in clock)
    if query.periods is not None and not set(query.periods) <= set(available):
        raise ValueError(f"Requested periods unavailable for match {match_id}")
    events = normalize_events(payload, match_id=match_id)
    passes = normalize_passes(payload, match_id=match_id)
    shots = normalize_shots(payload, match_id=match_id)
    creation_actions = normalize_creation_events(payload, match_id=match_id)
    evolution = build_match_evolution(
        events,
        shots,
        normalize_event_relations(payload, match_id=match_id),
        creation_actions,
        clock,
        provider=query.provider,
        match_id=str(match_id),
        team_ids=team_ids,
        events_sha256=events_sha256,
    )
    # Unresolved goal evidence makes all score filtering unavailable.
    if evolution.goal_evidence.status != "resolved":
        return ScoreStateContext(evolution, {})
    timeline = {e.external_event_id: e for e in evolution.events}
    actions: list[MatchPass | MatchShot | MatchDribble] = [
        *passes,
        *shots,
        *normalize_dribbles(payload, match_id=match_id),
    ]
    selected = []
    for action in actions:
        if action.period == 5:
            continue
        context = timeline[action.external_event_id]
        if context.score_state == query.score_state and (
            query.periods is None or action.period in query.periods
        ):
            selected.append(action)
    summary = build_offensive_summary(
        selected,
        provider=query.provider,
        match_id=str(match_id),
        events_sha256=events_sha256,
        available_periods=available,
        periods=query.periods,
    )
    scopes = {}
    if any(get_metric(m).family == "creation" for m in query.metrics):
        classification = evolution.creation_evidence
        shot_order = {shot.external_event_id: shot.source_index for shot in shots}
        for team in team_ids:
            scoped_passes = [
                action
                for action in creation_actions
                if isinstance(action, MatchPassCreation)
                and action.period != 5
                and action.external_team_id == team
                and timeline[action.external_event_id].score_state == query.score_state
                and (query.periods is None or action.period in query.periods)
            ]
            selected_ids = {action.external_event_id for action in scoped_passes}
            pairs = tuple(
                pair
                for pair in classification.classifications
                if pair.external_pass_event_id in selected_ids
            )
            scopes[team] = ScoreStateCreationEvidence(
                team_id=team,
                summary=_summarize_passes(
                    scoped_passes, classification.classifications, shot_order
                ),
                pairs=pairs,
                classification_policy_version=classification.policy_version,
                link_policy_version=classification.resolution.policy_version,
                match_reference_issues=classification.resolution.reference_issues,
            )
    defense = (
        prepare_score_state_defense(
            payload, evolution, state=query.score_state, periods=query.periods
        )
        if any(get_metric(m).family in DEFENSIVE_SCORE_FAMILIES for m in query.metrics)
        else {}
    )
    keepers = (
        prepare_score_state_goalkeepers(
            payload,
            evolution,
            state=query.score_state,
            periods=query.periods,
            population=keeper_population,
        )
        if any(get_metric(m).family == "goalkeepers" for m in query.metrics)
        else {}
    )
    families = frozenset(get_metric(m).family for m in query.metrics) & COMPLEMENTARY_SCORE_FAMILIES
    complementary, complementary_scopes = (
        prepare_score_state_complementary(
            payload,
            evolution,
            state=query.score_state,
            periods=query.periods,
            available_periods=available,
            families=families,
        )
        if families
        else ({}, ())
    )
    return ScoreStateContext(
        evolution,
        {t.external_team_id: t for t in summary.teams},
        scopes,
        defense,
        keepers,
        complementary,
        complementary_scopes,
    )


def score_state_value(identifier: str, context: ScoreStateContext, team_id: str) -> MetricValue:
    # Shared scalar conversion preserves existing ratio/xG coverage policies.
    from football_analytics.analytics.executor import _value

    if team_id not in context.evolution.team_ids:
        raise ValueError("Team is outside score source")
    if get_metric(identifier).family not in SCORE_STATE_FAMILIES:
        raise ValueError("Unsupported score-state metric")
    issues = context.evolution.issues
    if context.evolution.goal_evidence.status == "review":
        return MetricValue(
            metric_id=identifier,
            unit=get_metric(identifier).unit,
            status="unknown",
            value=None,
            reasons=("unresolved_score_state",),
            review_reasons=issues,
            coverage=Coverage(
                source_complete=True, population="unknown", eligible_events=0, covered_events=0
            ),
        )
    if get_metric(identifier).family in COMPLEMENTARY_SCORE_FAMILIES:
        family = get_metric(identifier).family
        if family not in context.complementary:
            raise ValueError("Complementary context was not prepared for query")
        value = complementary_value(identifier, context.complementary[family], team_id, None)
        return value.model_copy(
            update={"review_reasons": tuple(sorted(set(value.review_reasons) | set(issues)))}
        )
    if get_metric(identifier).family == "goalkeepers":
        keeper_scope = context.goalkeeper_scopes.get(team_id)
        if keeper_scope is None:
            raise ValueError("Keeper context was not prepared for query")
        reasons = set(issues)
        if keeper_scope.population == "unknown":
            return MetricValue(
                metric_id=identifier,
                unit=get_metric(identifier).unit,
                status="unknown",
                value=None,
                reasons=("goalkeeper_role_unknown",),
                review_reasons=tuple(sorted(reasons)),
                coverage=Coverage(
                    source_complete=True, population="unknown", eligible_events=0, covered_events=0
                ),
            )
        selection = dict(get_metric(identifier).selection)["scope"]
        counts = getattr(keeper_scope, selection)
        if counts.review_pairs:
            reasons.add("goalkeeper_pairs_require_review")
        if keeper_scope.match_source_issues or keeper_scope.unlinked_opponent_shot_event_ids:
            reasons.add("goalkeeper_source_link_issues_in_match")
        if keeper_scope.opponent_shots_without_selected_pair_ids:
            reasons.add("goalkeeper_opponent_shots_without_selected_pair")
        if keeper_scope.match_unlinked_shot_action_ids:
            reasons.add("unlinked_goalkeeper_shot_action_in_match")
        return MetricValue(
            metric_id=identifier,
            unit=get_metric(identifier).unit,
            status="available",
            value=getattr(counts, identifier.rsplit(".", 1)[-1]),
            review_reasons=tuple(sorted(reasons)),
            coverage=Coverage(
                source_complete=True,
                population="presence_confirmed",
                eligible_events=counts.pair_count,
                covered_events=counts.pair_count,
            ),
        )
    if get_metric(identifier).family in DEFENSIVE_SCORE_FAMILIES:
        defensive_scope = context.defensive_scopes.get(team_id)
        if defensive_scope is None:
            raise ValueError("Defensive context was not prepared for this query")
        value = defensive_value(identifier, defensive_scope.summary, "presence_confirmed")
        reasons = set(value.review_reasons) | set(issues)
        if defensive_scope.missing_related_target_ids:
            reasons.add("defensive_related_targets_missing")
        return value.model_copy(update={"review_reasons": tuple(sorted(reasons))})
    if get_metric(identifier).family == "creation":
        scope = context.creation_scopes.get(team_id)
        if scope is None:
            raise ValueError("Creation context was not prepared for this query")
        summary = scope.summary
        reasons = set(issues)
        if scope.match_reference_issues:
            reasons.add("creation_reference_issues_in_match")
        if summary.counts.review_pairs:
            reasons.add("creation_pairs_require_review")
        if summary.flagged_unlinked_pass_event_ids:
            reasons.add("creation_positive_flags_without_link")
        return MetricValue(
            metric_id=identifier,
            unit=get_metric(identifier).unit,
            status="available",
            value=cast(int, getattr(summary.counts, identifier.rsplit(".", 1)[-1])),
            review_reasons=tuple(sorted(reasons)),
            coverage=Coverage(
                source_complete=True,
                population="presence_confirmed",
                eligible_events=summary.counts.pass_count,
                covered_events=summary.counts.pass_count,
            ),
        )
    value = _value(identifier, context.teams.get(team_id), "presence_confirmed")
    return value.model_copy(
        update={"review_reasons": tuple(sorted({*value.review_reasons, *issues}))}
    )


def _match_score_rows(
    connection: Connection,
    query: ScoreStateQuery,
    match_id: int,
) -> tuple[SourceReference, list[ResultRow], ScoreStateEvidence]:
    from football_analytics.analytics.executor import load_query_sources

    source, events, lineup_payload = load_query_sources(connection, match_id=match_id)
    if source.participation_calculation_id is None:
        raise ValueError("Participation provenance is required")
    players = reconstruct_match_participation(lineup_payload, events, match_id)
    verify_stored_participation(connection, source.participation_calculation_id, players)
    lineups = normalize_lineups(lineup_payload, match_id, starting_xi=normalize_starting_xi(events))
    teams = sorted({p.external_team_id for p in lineups})
    if len(teams) != 2:
        raise ValueError("Two source lineup teams required")
    keeper_population = (
        goalkeeper_team_population(
            events,
            lineup_payload,
            match_id=match_id,
            events_sha256=source.events_sha256,
            lineups_sha256=source.lineups_sha256 or "",
            periods=query.periods,
            players=players,
        )
        if any(get_metric(m).family == "goalkeepers" for m in query.metrics)
        else None
    )
    context = prepare_score_state(
        events,
        query=query,
        match_id=match_id,
        team_ids=(teams[0], teams[1]),
        events_sha256=source.events_sha256,
        keeper_population=keeper_population,
    )
    if source.available_periods != tuple(p.period for p in context.evolution.periods):
        raise ValueError("Score clock differs from source periods")
    rows = [
        ResultRow(
            match_ids=(match_id,),
            team_id=team,
            values=tuple(score_state_value(m, context, team) for m in query.metrics),
        )
        for team in teams
        if not query.team_ids or team in query.team_ids
    ]
    evidence = ScoreStateEvidence(
        match_id=match_id,
        events_sha256=source.events_sha256,
        team_ids=(teams[0], teams[1]),
        goal_status=context.evolution.goal_evidence.status,
        final_score=context.evolution.events[-1].score_after if context.evolution.events else None,
        issues=context.evolution.issues,
        creation_scope=tuple(context.creation_scopes.values()),
        defensive_scope=tuple(context.defensive_scopes.values()),
        goalkeeper_scope=tuple(context.goalkeeper_scopes.values()),
        complementary_scope=context.complementary_scopes,
    )
    return source, rows, evidence


def execute_score_state_query(connection: Connection, query: ScoreStateQuery) -> ScoreStateResult:
    from football_analytics.analytics.executor import combine_values

    # Revalidate even model_copy/model_construct inputs before reading sources.
    query = ScoreStateQuery.model_validate_json(query.model_dump_json())
    sources, rows, evidence = [], [], []
    for match_id in query.match_ids:
        source, selected, proof = _match_score_rows(connection, query, match_id)
        sources.append(source)
        rows.extend(selected)
        evidence.append(proof)
    if not set(query.team_ids) <= {r.team_id for r in rows}:
        raise ValueError("Requested team has no rows in selected matches")
    if query.aggregation == "selected_matches":
        groups: dict[str, list[ResultRow]] = defaultdict(list)
        for row in rows:
            groups[row.team_id].append(row)
        rows = [
            ResultRow(
                match_ids=query.match_ids,
                team_id=team,
                values=tuple(
                    combine_values(
                        [next(v for v in row.values if v.metric_id == metric) for row in group]
                    )
                    for metric in query.metrics
                ),
            )
            for team, group in sorted(groups.items())
        ]
    return ScoreStateResult(
        query=query,
        sources=tuple(sources),
        rows=tuple(rows),
        score_evidence=tuple(evidence),
        score_state_contract_version=query.score_state_contract_version,
    )
