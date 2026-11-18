"""Defensive projections filtered by source-event score, with related IDs preserved."""

from typing import Literal, Self

from pydantic import model_validator

from football_analytics.analytics.contracts import Contract
from football_analytics.ingestion.defensive_scope import DefensiveEvent
from football_analytics.ingestion.defensive_summary import (
    TeamDefensiveSummary,
    build_defensive_summary,
)
from football_analytics.ingestion.match_evolution import (
    MatchEvolution,
    select_score_state_events,
)
from football_analytics.integrations.statsbomb.aerial_duels import normalize_aerial_duels
from football_analytics.integrations.statsbomb.ball_recoveries import normalize_ball_recoveries
from football_analytics.integrations.statsbomb.blocks_and_clearances import (
    normalize_blocks_and_clearances,
)
from football_analytics.integrations.statsbomb.duels import normalize_duels
from football_analytics.integrations.statsbomb.event_relations import normalize_event_relations
from football_analytics.integrations.statsbomb.interceptions import normalize_interceptions
from football_analytics.integrations.statsbomb.pressures_and_fouls import (
    normalize_pressures_and_fouls,
)

DEFENSIVE_SCORE_FAMILIES = frozenset({"defense", "duels", "aerial"})


class ScoreStateDefensiveEvidence(Contract):
    scope_policy_version: Literal["1.0.0"] = "1.0.0"
    scope_basis: Literal["source_event_period_and_pre_action_score"] = (
        "source_event_period_and_pre_action_score"
    )
    team_id: str
    summary: TeamDefensiveSummary | None
    action_event_ids: tuple[str, ...]
    aerial_event_ids: tuple[str, ...]
    shared_event_ids: tuple[str, ...]
    unique_source_event_ids: tuple[str, ...]
    related_ids_by_source: dict[str, tuple[str, ...] | None]
    related_targets_outside_selected_union: tuple[str, ...]
    missing_related_target_ids: tuple[str, ...]

    @model_validator(mode="after")
    def check_source_union(self) -> Self:
        for ids in (
            self.action_event_ids,
            self.aerial_event_ids,
            self.shared_event_ids,
            self.unique_source_event_ids,
        ):
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate defensive evidence source")
        actions, aerials = set(self.action_event_ids), set(self.aerial_event_ids)
        union = set(self.unique_source_event_ids)
        if (
            union != actions | aerials
            or set(self.shared_event_ids) != actions & aerials
            or set(self.related_ids_by_source) != union
        ):
            raise ValueError("Defensive evidence projections do not recompose source union")
        if self.summary is None:
            if union:
                raise ValueError("Nonempty defensive evidence requires a summary")
        elif (
            self.summary.external_team_id != self.team_id
            or self.summary.external_event_ids != self.unique_source_event_ids
        ):
            raise ValueError("Defensive summary differs from scope identity or source IDs")
        targets = {target for ids in self.related_ids_by_source.values() for target in ids or ()}
        if (
            set(self.missing_related_target_ids) & set(self.related_targets_outside_selected_union)
            or set(self.missing_related_target_ids)
            | set(self.related_targets_outside_selected_union)
            != targets - union
        ):
            raise ValueError("Related-target diagnostics differ from preserved references")
        return self


def prepare_score_state_defense(
    payload: object,
    evolution: MatchEvolution,
    *,
    state: Literal["leading", "drawing", "trailing"],
    periods: tuple[int, ...] | None,
) -> dict[str, ScoreStateDefensiveEvidence]:
    match_id = int(evolution.external_match_id)
    if evolution.goal_evidence.status != "resolved":
        raise ValueError("Cannot select defensive score scope from unresolved goals")
    relations = {
        e.external_event_id: e for e in normalize_event_relations(payload, match_id=match_id)
    }
    timeline = {e.external_event_id: e for e in evolution.events}
    if set(relations) != set(timeline) | set(evolution.excluded_shootout_event_ids):
        raise ValueError("Defensive payload coverage differs from score timeline")
    for key, event in timeline.items():
        relation = relations[key]
        if (relation.provider, relation.external_match_id) != (
            evolution.provider,
            evolution.external_match_id,
        ) or any(
            getattr(event, field) != getattr(relation, field)
            for field in (
                "source_index",
                "period",
                "timestamp_ms",
                "event_type",
                "external_team_id",
                "external_player_id",
            )
        ):
            raise ValueError("Defensive payload header differs from score timeline")
    actions: list[DefensiveEvent] = [
        *normalize_duels(payload, match_id=match_id),
        *normalize_interceptions(payload, match_id=match_id),
        *normalize_ball_recoveries(payload, match_id=match_id),
        *normalize_blocks_and_clearances(payload, match_id=match_id),
        *normalize_pressures_and_fouls(payload, match_id=match_id),
    ]
    aerials = normalize_aerial_duels(payload, match_id=match_id)
    selected_actions = [
        action
        for team in evolution.team_ids
        for action in select_score_state_events(
            actions,
            evolution,
            team_id=team,
            state=state,
            periods=periods,
        )
    ]
    selected_aerials = [
        action
        for team in evolution.team_ids
        for action in select_score_state_events(
            aerials,
            evolution,
            team_id=team,
            state=state,
            periods=periods,
        )
    ]
    summary = build_defensive_summary(
        selected_actions,
        selected_aerials,
        provider=evolution.provider,
        match_id=evolution.external_match_id,
        events_sha256=evolution.events_sha256,
        available_periods=tuple(p.period for p in evolution.periods),
        periods=periods,
    )
    by_team = {team.external_team_id: team for team in summary.teams}
    relations = {
        e.external_event_id: e for e in normalize_event_relations(payload, match_id=match_id)
    }
    result = {}
    for team in evolution.team_ids:
        team_summary = by_team.get(team)
        union = team_summary.external_event_ids if team_summary else ()
        ids = set(union)
        related = {key: relations[key].related_event_ids for key in union}
        targets = {target for references in related.values() for target in references or ()}
        result[team] = ScoreStateDefensiveEvidence(
            team_id=team,
            summary=team_summary,
            action_event_ids=tuple(key for key in summary.action_event_ids if key in ids),
            aerial_event_ids=tuple(key for key in summary.aerial_event_ids if key in ids),
            shared_event_ids=tuple(key for key in summary.shared_event_ids if key in ids),
            unique_source_event_ids=union,
            related_ids_by_source=related,
            related_targets_outside_selected_union=tuple(sorted((targets & set(relations)) - ids)),
            missing_related_target_ids=tuple(sorted(targets - set(relations))),
        )
    return result
