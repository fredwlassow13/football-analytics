"""Structured analyses and pending capabilities; deliberately not scalar metrics."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from football_analytics.analytics.capabilities import STRUCTURED_EXECUTOR_RESOURCES

_CONTRACT_RESOURCES = frozenset(
    {
        "analysis.pass_connections",
        "analysis.defensive_map",
        "analysis.possession_fragments",
        "analysis.attacks",
        "analysis.tactical_windows",
    }
)


class AnalysisResource(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    id: str
    kind: Literal["structured_analysis", "diagnostic", "visualization"]
    implementation: str
    output_schema: str | None = None
    grain: str
    limitations: str
    tests: tuple[str, ...]
    execution_available: bool = False

    query_contract: str | None = None
    result_contract: str | None = None
    contract_version: Literal["1.0.0"] | None = None

    @property
    def contract_available(self) -> bool:
        return self.query_contract is not None

    @model_validator(mode="after")
    def check_contract_metadata(self) -> Self:
        present = tuple(
            value is not None
            for value in (
                self.query_contract,
                self.result_contract,
                self.contract_version,
            )
        )
        if any(present) and not all(present):
            raise ValueError("Query/result contract and version must be supplied together")
        if self.execution_available and not self.contract_available:
            raise ValueError("Execution requires registered query/result contracts")
        return self


class PendingCapability(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    id: str
    status: Literal["pending", "unsupported_by_current_adapter"]
    reason: str


def _resource(
    identifier: str,
    module: str,
    function: str,
    schema: str | None,
    grain: str,
    limits: str,
    *,
    diagnostic: bool = False,
    visualization: bool = False,
) -> AnalysisResource:
    prefix = f"football_analytics.ingestion.{module}."
    return AnalysisResource(
        id=identifier,
        execution_available=identifier in STRUCTURED_EXECUTOR_RESOURCES,
        query_contract="football_analytics.analytics.structured_contracts.StructuredQuery"
        if identifier in _CONTRACT_RESOURCES
        else None,
        result_contract="football_analytics.analytics.structured_contracts.StructuredResult"
        if identifier in _CONTRACT_RESOURCES
        else None,
        contract_version="1.0.0" if identifier in _CONTRACT_RESOURCES else None,
        kind="diagnostic"
        if diagnostic
        else "visualization"
        if visualization
        else "structured_analysis",
        implementation=prefix + function,
        output_schema=prefix + schema if schema else None,
        grain=grain,
        limitations=limits,
        tests=(f"tests/test_{module}.py",),
    )


RESOURCES: tuple[AnalysisResource, ...] = (
    _resource(
        "analysis.offensive_roster",
        "offensive_roster",
        "build_offensive_roster",
        "OffensiveRosterSummary",
        "listed player/period",
        "Observed, zero with presence, not applicable and unknown remain distinct.",
    ),
    _resource(
        "analysis.defensive_roster",
        "defensive_roster",
        "build_defensive_roster",
        "DefensiveRosterSummary",
        "listed player/period",
        "Roster and participation evidence are required before inferring zero defensive actions.",
    ),
    _resource(
        "analysis.goalkeeper_links",
        "goalkeeper_shot_links",
        "resolve_goalkeeper_shot_links",
        "GoalkeeperShotResolution",
        "goalkeeper/shot pair",
        "Preserves directional links, ambiguities and unresolved references.",
        diagnostic=True,
    ),
    _resource(
        "analysis.creation_links",
        "creation_links",
        "resolve_creation_links",
        "CreationLinkResolution",
        "pass/shot pair",
        "Explicit assisted_shot_id/key_pass_id references; no temporal inference.",
        diagnostic=True,
    ),
    _resource(
        "analysis.team_tactical_timeline",
        "team_tactical_timeline",
        "build_team_tactical_timeline",
        None,
        "team/player/time segment",
        "Recorded tactical evidence plus participation, not continuous position tracking.",
    ),
    _resource(
        "analysis.defensive_breakdowns",
        "defensive_statistics",
        "summarize_defensive_actions",
        "DefensiveActionStatistics",
        "event type / flag / category",
        "Includes interception/duel outcomes, foul types/cards, clearance body parts, "
        "flag true/false/unknown and field present/missing. Categories retain native values; "
        "not all outcomes mean success.",
    ),
    _resource(
        "analysis.offensive_summary",
        "offensive_summary",
        "build_offensive_summary",
        "OffensiveSummary",
        "player/team/period",
        "Observed events only; not roster-wide zero.",
    ),
    _resource(
        "analysis.defensive_summary",
        "defensive_summary",
        "build_defensive_summary",
        "DefensiveSummary",
        "player/team/period",
        "Union of source IDs, including aerials. "
        "Do not add overlapping action/aerial counts to obtain a unique total.",
    ),
    _resource(
        "analysis.goalkeeper_actions",
        "goalkeeper_statistics",
        "build_goalkeeper_statistics",
        "GoalkeeperStatisticsResult",
        "goalkeeper/team/action type/outcome",
        "Includes non-shot interventions and penalty/non-penalty/unknown groups; "
        "not PSxG or goals prevented.",
    ),
    _resource(
        "analysis.creation",
        "creation_statistics",
        "build_creation_statistics",
        "CreationStatisticsResult",
        "pass/shot pair and player/team",
        "Flags true/false/unknown, explicit links, unlinked and cross-scope issues; not xA.",
    ),
    _resource(
        "analysis.movement_geometry",
        "movement_geometry",
        "analyze_movement_geometry",
        "MovementGeometry",
        "Pass/Carry event",
        "Signed dx/dy, endpoint distance, goal-distance "
        "reduction, direction and zone entries. Source units, not physical travelled metres.",
    ),
    _resource(
        "analysis.ball_control_cells",
        "ball_control_statistics",
        "build_ball_control_statistics",
        "BallControlStatisticsResult",
        "player/team/category/outcome/zone/pressure",
        "Evidence categories are not automatic possession gains/losses; unknowns preserved.",
    ),
    _resource(
        "analysis.possession_fragments",
        "possession_sequences",
        "build_possession_sequences",
        "PossessionSequenceResult",
        "source fragment",
        "Source labels and conservative continuity; "
        "observed timestamp span is not real possession duration.",
    ),
    _resource(
        "analysis.possession_timing",
        "possession_timing",
        "audit_possession_timing",
        "PossessionTimingAudit",
        "event/fragment",
        "Duration/end-time gaps and overlaps are "
        "diagnostics; they do not establish possession percentage.",
        diagnostic=True,
    ),
    _resource(
        "analysis.control_fragments",
        "control_sequence_analysis",
        "build_control_sequence_analysis",
        "ControlSequenceAnalysis",
        "fragment/event relation",
        "Pressure, shots/goals/own goals, "
        "owner vs shooting team and transitions; conservative source observations.",
    ),
    _resource(
        "analysis.attacks",
        "attack_sequences",
        "build_attack_sequences",
        "AttackSequenceResult",
        "observed attack/stoppage",
        "Operational boundaries, stoppages and review status; "
        "possession duration/percentage still unavailable.",
    ),
    _resource(
        "analysis.play_phases",
        "play_phase",
        "build_play_phases",
        "PlayPhaseResult",
        "event/fragment",
        "Open play / set-piece context / unknown; continuation can retain "
        "set-piece context. Not yet a filter in MetricQuery.",
    ),
    _resource(
        "analysis.pass_connections",
        "pass_network",
        "build_pass_connections",
        "PassConnection",
        "directed player pair",
        "Completed passes with recipient; edges are not player totals.",
    ),
    _resource(
        "analysis.pass_nodes",
        "pass_network_nodes",
        "build_pass_network_nodes",
        "PassNetworkNode",
        "player node",
        "Mean origins of attempted passes, not average tracking position.",
    ),
    _resource(
        "analysis.window_networks",
        "window_pass_network",
        "prepare_window_pass_networks",
        "WindowPassNetwork",
        "tactical window",
        "Depends on window participation and event clocks.",
    ),
    _resource(
        "analysis.tactical_changes",
        "tactical_changes",
        "compare_tactical_snapshots",
        "TacticalChanges",
        "snapshot pair",
        "Changes in recorded formations and positions; not continuous tracking.",
    ),
    _resource(
        "analysis.tactical_windows",
        "tactical_window_catalogue",
        "build_tactical_window_catalogue",
        "TacticalWindow",
        "window",
        "Zero-duration windows are retained, not hidden.",
    ),
    _resource(
        "analysis.window_lineups",
        "tactical_window_lineups",
        "build_window_lineups",
        "WindowTeamLineup",
        "team/window/player",
        "Evidence-based window membership.",
    ),
    _resource(
        "analysis.event_clock_quality",
        "tactical_window_quality",
        "audit_event_clocks",
        "EventClockIssue",
        "event",
        "Source order and timestamps may differ.",
        diagnostic=True,
    ),
    _resource(
        "analysis.occupancy",
        "team_occupancy",
        "build_team_occupancy",
        "TeamOccupancyInterval",
        "team/time interval",
        "Players on field; not ball-in-play time or possession.",
    ),
    _resource(
        "analysis.own_goal_links",
        "own_goal_links",
        "resolve_own_goal_links",
        "OwnGoalLinkResult",
        "own-goal source pair",
        "Two source perspectives must not be counted as two goals.",
    ),
    _resource(
        "analysis.score_reconciliation",
        "score_reconciliation",
        "reconcile_match_score",
        "ScoreReconciliation",
        "team/match",
        "Shot goals + usable own goals; ambiguity requires review.",
    ),
    _resource(
        "analysis.shootout_roster",
        "shootout_roster",
        "build_shootout_roster",
        "ShootoutRosterPlayer",
        "listed player/shootout",
        "Observed kicker/keeper roles are distinct from eligibility.",
    ),
    _resource(
        "analysis.shootout_eligibility",
        "shootout_eligibility",
        "assess_initial_shootout_eligibility",
        "ShootoutEligibilityAssessment",
        "player/initial shootout context",
        "Coverage review bound to hashes; no dynamic eligibility through all kicks.",
    ),
    _resource(
        "analysis.pass_heatmap",
        "player_pass_heatmap",
        "build_player_pass_heatmap",
        "PlayerPassHeatmap",
        "player/grid cell",
        "Counts/distribution, not time spent in location.",
        visualization=True,
    ),
    _resource(
        "analysis.activity_heatmap",
        "player_activity_heatmap",
        "build_player_activity_heatmap",
        "PlayerActivityHeatmap",
        "player/grid cell",
        "Observed actions, not tracking or touches inferred.",
        visualization=True,
    ),
    _resource(
        "analysis.defensive_map",
        "defensive_map",
        "build_defensive_map",
        "DefensiveMap",
        "player/team/type/group/period/grid cell",
        "Overlapping groups deduplicate by source ID; "
        "missing/outside coordinates retained in quality diagnostics.",
        visualization=True,
    ),
    _resource(
        "analysis.tournament_baseline",
        "tournament_baseline",
        "build_tournament_baseline",
        "TournamentBaseline",
        "team/match and selected-match totals",
        "Per-match means include "
        "extra time; requires reconciled scores and complete xG. Not per-90.",
    ),
)

# Renderers are existing presentation assets, not new interfaces or scalar metrics.
RESOURCES += tuple(
    AnalysisResource(
        id=identifier,
        kind="visualization",
        implementation=f"football_analytics.visualization.{module}.{function}",
        grain=grain,
        limitations=limits,
        tests=(f"tests/test_{module}.py",),
    )
    for identifier, module, function, grain, limits in (
        ("plot.shots", "shot_map", "render_shot_map", "shot point", "Recorded shot coordinates."),
        (
            "plot.xg_shots",
            "shot_map",
            "render_xg_shot_map",
            "shot point",
            "Marker area uses source xG; absence remains visible.",
        ),
        (
            "plot.goal_view",
            "goal_view",
            "render_goal_view",
            "shot endpoint",
            "Goal-plane endpoint, not reconstructed trajectory.",
        ),
        (
            "plot.off_target",
            "off_target_view",
            "render_off_target_view",
            "shot endpoint",
            "Source outcome and endpoint quality remain distinct.",
        ),
        (
            "plot.pass_network",
            "pass_network",
            "render_pass_network",
            "node/edge",
            "Layout from observed pass origins, not tracking.",
        ),
    )
)

PENDING_CAPABILITIES: tuple[PendingCapability, ...] = (
    PendingCapability(
        id="defense.sliding_tackles",
        status="unsupported_by_current_adapter",
        reason="Current normalized Duel supports Tackle/Aerial Lost, not a sliding subtype.",
    ),
    PendingCapability(
        id="possession.duration_and_percentage",
        status="pending",
        reason="Observed fragments/spans and stoppage reconstruction "
        "do not establish true duration.",
    ),
    PendingCapability(
        id="analysis.score_state_evolution",
        status="pending",
        reason="No completed shared metric service by score situation.",
    ),
    PendingCapability(
        id="analysis.cumulative_xg_creation",
        status="pending",
        reason="Source xG and creation links exist; validated evolution service remains pending.",
    ),
    PendingCapability(
        id="shootout.dynamic_eligibility",
        status="pending",
        reason="Initial-context assessment exists; changes through kicks are not completed.",
    ),
    PendingCapability(
        id="metrics.per_90",
        status="pending",
        reason="Exposure/denominator policy and validation must precede rankings.",
    ),
    PendingCapability(
        id="creation.xa_and_goalkeeper_psxg",
        status="pending",
        reason="No implemented xA/PSxG model or definition identified in the audited modules.",
    ),
)
