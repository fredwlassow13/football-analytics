"""Keeper pair counts attributed by defending-team score before the linked shot."""

from typing import Literal, Self

from pydantic import model_validator

from football_analytics.analytics.contracts import Contract
from football_analytics.analytics.defensive_execution import prepare_defense
from football_analytics.ingestion.goalkeeper_shot_classification import (
    EXPECTED_KEEPER_TYPES,
    GoalkeeperShotClassification,
    classify_goalkeeper_shots,
)
from football_analytics.ingestion.goalkeeper_shot_links import RelationshipIssue
from football_analytics.ingestion.match_evolution import MatchEvolution
from football_analytics.ingestion.match_participation import (
    PlayerParticipation,
    reconstruct_match_participation,
)
from football_analytics.ingestion.player_goalkeeper_statistics import GoalkeeperShotCounts, _counts
from football_analytics.ingestion.player_scope_participation import scope_player_participation
from football_analytics.integrations.statsbomb.event_relations import normalize_event_relations
from football_analytics.integrations.statsbomb.goalkeepers import normalize_goalkeeper_actions
from football_analytics.integrations.statsbomb.lineups import normalize_lineups
from football_analytics.integrations.statsbomb.period_clock import normalize_period_clock
from football_analytics.integrations.statsbomb.shots import normalize_shots
from football_analytics.integrations.statsbomb.starting_xi import normalize_starting_xi

TeamKeeperPopulation = Literal["presence_confirmed", "unknown"]


class ScoreStateGoalkeeperEvidence(Contract):
    scope_policy_version: Literal["1.0.0"] = "1.0.0"
    scope_basis: Literal["shot_period_and_defending_team_pre_shot_score"] = (
        "shot_period_and_defending_team_pre_shot_score"
    )
    classification_policy_version: Literal["1.0.0"] = "1.0.0"
    link_policy_version: Literal["1.0.0"] = "1.0.0"
    team_id: str
    population: TeamKeeperPopulation
    counts: GoalkeeperShotCounts
    penalty_counts: GoalkeeperShotCounts
    non_penalty_counts: GoalkeeperShotCounts
    unknown_penalty_counts: GoalkeeperShotCounts
    pairs: tuple[GoalkeeperShotClassification, ...]
    goalkeeper_event_ids: tuple[str, ...]
    candidate_shot_event_ids: tuple[str, ...]
    selected_opponent_shot_event_ids: tuple[str, ...]
    unlinked_opponent_shot_event_ids: tuple[str, ...]
    opponent_shots_without_selected_pair_ids: tuple[str, ...]
    match_unlinked_goalkeeper_event_ids: tuple[str, ...]
    match_unlinked_shot_action_ids: tuple[str, ...]
    match_source_issues: tuple[RelationshipIssue, ...]
    match_excluded_shootout_pairs: tuple[tuple[str, str], ...]

    @model_validator(mode="after")
    def check_partitions(self) -> Self:
        keys = {(p.external_goalkeeper_event_id, p.external_shot_event_id) for p in self.pairs}
        if len(keys) != len(self.pairs) or any(
            p.shot_period == 5 or p.goalkeeper_period == 5 for p in self.pairs
        ):
            raise ValueError("Duplicate keeper pair or shootout leakage")
        if self.counts != _counts(self.pairs):
            raise ValueError("Keeper counts differ from preserved pairs")
        for flag, counts in (
            (True, self.penalty_counts),
            (False, self.non_penalty_counts),
            (None, self.unknown_penalty_counts),
        ):
            if counts != _counts([p for p in self.pairs if p.is_penalty is flag]):
                raise ValueError("Keeper penalty partitions differ from preserved pairs")
        if set(self.goalkeeper_event_ids) != {
            p.external_goalkeeper_event_id for p in self.pairs
        } or set(self.candidate_shot_event_ids) != {p.external_shot_event_id for p in self.pairs}:
            raise ValueError("Keeper pair IDs differ from source lists")
        for ids in (
            self.goalkeeper_event_ids,
            self.candidate_shot_event_ids,
            self.selected_opponent_shot_event_ids,
            self.unlinked_opponent_shot_event_ids,
        ):
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate keeper scope evidence ID")
        if not set(self.unlinked_opponent_shot_event_ids) <= set(
            self.selected_opponent_shot_event_ids
        ):
            raise ValueError("Unlinked shots outside selected opponent-shot population")
        if set(self.opponent_shots_without_selected_pair_ids) != (
            set(self.selected_opponent_shot_event_ids) - set(self.candidate_shot_event_ids)
        ):
            raise ValueError("Opponent-shot pair coverage is inconsistent")
        return self


def goalkeeper_team_population(
    payload: object,
    lineup_payload: object,
    *,
    match_id: int,
    events_sha256: str,
    lineups_sha256: str,
    periods: tuple[int, ...] | None,
    players: list[PlayerParticipation] | None = None,
) -> dict[str, TeamKeeperPopulation]:
    """Reuse the established role/participation policy; no new keeper-role inference."""
    clock = normalize_period_clock(payload)
    available = tuple(p.period for p in clock)
    participants = (
        reconstruct_match_participation(lineup_payload, payload, match_id)
        if players is None
        else players
    )
    scoped = scope_player_participation(
        participants, clock, match_id=str(match_id), selected_periods=periods
    )
    lineups = normalize_lineups(
        lineup_payload, match_id, starting_xi=normalize_starting_xi(payload)
    )
    existing = prepare_defense(
        payload,
        match_id=match_id,
        events_sha256=events_sha256,
        lineups_sha256=lineups_sha256,
        available_periods=available,
        periods=periods,
        lineups=lineups,
        players=participants,
        scoped=scoped,
        clock=clock,
        needs_defense=False,
        needs_keepers=True,
    )
    result: dict[str, TeamKeeperPopulation] = {}
    for team in {p.external_team_id for p in scoped}:
        states = [
            existing.roles[p.external_player_id] for p in scoped if p.external_team_id == team
        ]
        result[team] = (
            "presence_confirmed"
            if any(s in {"observed", "presence_confirmed"} for s in states)
            else "unknown"
        )
    return result


def prepare_score_state_goalkeepers(
    payload: object,
    evolution: MatchEvolution,
    *,
    state: Literal["leading", "drawing", "trailing"],
    periods: tuple[int, ...] | None,
    population: dict[str, TeamKeeperPopulation] | None,
) -> dict[str, ScoreStateGoalkeeperEvidence]:
    if evolution.goal_evidence.status != "resolved":
        raise ValueError("Cannot select keeper scope from unresolved score")
    match_id = int(evolution.external_match_id)
    available = {p.period for p in evolution.periods}
    selected = available if periods is None else set(periods)
    if not selected or not selected <= available:
        raise ValueError("Keeper shot periods unavailable")
    keepers = normalize_goalkeeper_actions(payload, match_id=match_id)
    shots = normalize_shots(payload, match_id=match_id)
    relations = normalize_event_relations(payload, match_id=match_id)
    timeline = {e.external_event_id: e for e in evolution.events}
    if {e.external_event_id for e in relations} != set(timeline) | set(
        evolution.excluded_shootout_event_ids
    ):
        raise ValueError("Keeper payload coverage differs from timeline")
    for relation in relations:
        if relation.period == 5:
            continue
        event = timeline[relation.external_event_id]
        if any(
            getattr(event, f) != getattr(relation, f)
            for f in (
                "source_index",
                "period",
                "timestamp_ms",
                "event_type",
                "external_team_id",
                "external_player_id",
            )
        ):
            raise ValueError("Keeper source header differs from timeline")
    for action in keepers:
        if action.period != 5 and (
            action.external_team_id not in evolution.team_ids or action.external_player_id is None
        ):
            raise ValueError("Keeper actions require source team and player")
    classification = classify_goalkeeper_shots(
        keepers,
        shots,
        relations,
        provider=evolution.provider,
        match_id=evolution.external_match_id,
        events_sha256=evolution.events_sha256,
    )
    keepers_by_id = {a.external_event_id: a for a in keepers}
    shots_by_id = {s.external_event_id: s for s in shots}
    all_unlinked_shots = set(classification.resolution.unlinked_shot_event_ids)
    all_unlinked_keepers = set(classification.resolution.unlinked_goalkeeper_event_ids)
    shot_keeper_types = {kind for types in EXPECTED_KEEPER_TYPES.values() for kind in types}

    def in_scope(shot_id: str, team: str) -> bool:
        shot = shots_by_id[shot_id]
        if shot.period not in selected:
            return False
        before = timeline[shot_id].score_before
        if before is None:
            raise ValueError("Missing pre-shot score")
        own = evolution.team_ids.index(team)
        delta = before[own] - before[1 - own]
        observed = "leading" if delta > 0 else "trailing" if delta < 0 else "drawing"
        return observed == state

    result = {}
    for team in evolution.team_ids:
        pairs = tuple(
            p
            for p in classification.classifications
            if keepers_by_id[p.external_goalkeeper_event_id].external_team_id == team
            and p.shot_period != 5
            and p.goalkeeper_period != 5
            and in_scope(p.external_shot_event_id, team)
        )
        keeper_ids = {p.external_goalkeeper_event_id for p in pairs}
        shot_ids = {p.external_shot_event_id for p in pairs}
        opponent_shots = tuple(
            s.external_event_id
            for s in shots
            if s.period in selected
            and s.external_team_id != team
            and in_scope(s.external_event_id, team)
        )
        unlinked_actions = [
            a
            for a in keepers
            if a.external_team_id == team
            and a.period != 5
            and a.external_event_id in all_unlinked_keepers
        ]
        result[team] = ScoreStateGoalkeeperEvidence(
            team_id=team,
            population=(population or {}).get(team, "unknown"),
            counts=_counts(pairs),
            penalty_counts=_counts([p for p in pairs if p.is_penalty is True]),
            non_penalty_counts=_counts([p for p in pairs if p.is_penalty is False]),
            unknown_penalty_counts=_counts([p for p in pairs if p.is_penalty is None]),
            pairs=pairs,
            goalkeeper_event_ids=tuple(
                a.external_event_id for a in keepers if a.external_event_id in keeper_ids
            ),
            candidate_shot_event_ids=tuple(
                s.external_event_id for s in shots if s.external_event_id in shot_ids
            ),
            selected_opponent_shot_event_ids=opponent_shots,
            unlinked_opponent_shot_event_ids=tuple(
                key for key in opponent_shots if key in all_unlinked_shots
            ),
            opponent_shots_without_selected_pair_ids=tuple(
                key for key in opponent_shots if key not in shot_ids
            ),
            match_unlinked_goalkeeper_event_ids=tuple(
                a.external_event_id for a in unlinked_actions
            ),
            match_unlinked_shot_action_ids=tuple(
                a.external_event_id
                for a in unlinked_actions
                if a.goalkeeper_type in shot_keeper_types
            ),
            match_source_issues=classification.resolution.source_issues,
            match_excluded_shootout_pairs=tuple(
                (p.external_goalkeeper_event_id, p.external_shot_event_id)
                for p in classification.classifications
                if keepers_by_id[p.external_goalkeeper_event_id].external_team_id == team
                and (p.shot_period == 5 or p.goalkeeper_period == 5)
            ),
            classification_policy_version=classification.policy_version,
            link_policy_version=classification.resolution.policy_version,
        )
    return result
