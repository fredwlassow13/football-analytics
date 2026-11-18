from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.movement_progression import (
    MovementProgression,
    classify_movement_progression,
)
from football_analytics.ingestion.offensive_scope import select_offensive_periods
from football_analytics.integrations.base import (
    MatchEvent,
    MatchEventSpatial,
    MatchPass,
)

MovementMetricName = Literal[
    "eligible_for_count",
    "geometrically_progressive",
    "countable_progression",
    "countable_final_third_entry",
    "countable_penalty_area_entry",
]

METRICS: tuple[MovementMetricName, ...] = (
    "eligible_for_count",
    "geometrically_progressive",
    "countable_progression",
    "countable_final_third_entry",
    "countable_penalty_area_entry",
)


class MovementMetric(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    true: int = Field(ge=0)
    false: int = Field(ge=0)
    unknown: int = Field(ge=0)
    true_event_ids: tuple[str, ...]
    false_event_ids: tuple[str, ...]
    unknown_event_ids: tuple[str, ...]


class MovementTypeStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    event_type: Literal["Pass", "Carry"]
    event_count: int = Field(ge=0)
    external_event_ids: tuple[str, ...]
    geometry_unavailable_event_ids: tuple[str, ...]
    direction_event_ids: dict[str, tuple[str, ...]]
    pass_outcome_event_ids: dict[str, tuple[str, ...]]
    metrics: dict[MovementMetricName, MovementMetric]


class MovementStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    passes: MovementTypeStatistics
    carries: MovementTypeStatistics


class PlayerMovementStatistics(MovementStatistics):
    external_team_id: str
    external_player_id: str


class TeamMovementStatistics(MovementStatistics):
    external_team_id: str
    external_player_ids: tuple[str, ...]


class MovementStatisticsResult(MovementStatistics):
    provider: Literal["statsbomb"] = "statsbomb"
    external_match_id: str
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    formula_version: Literal["1.0.0"] = "1.0.0"
    classification_policy_version: Literal["1.0.0"] = "1.0.0"
    geometry_policy_version: Literal["1.0.0"] = "1.0.0"
    progression_policy_name: Literal["project_dx10_goal_distance25"] = (
        "project_dx10_goal_distance25"
    )
    population_basis: Literal["observed_movements"] = "observed_movements"
    scope_basis: Literal["movement_period"] = "movement_period"
    available_periods: tuple[int, ...]
    periods: tuple[int, ...]
    players: tuple[PlayerMovementStatistics, ...]
    teams: tuple[TeamMovementStatistics, ...]
    excluded_shootout_movement_event_ids: tuple[str, ...]


def _metric(
    rows: Sequence[MovementProgression],
    name: MovementMetricName,
) -> MovementMetric:
    true_ids = tuple(
        row.geometry.source.external_event_id for row in rows if getattr(row, name) is True
    )
    false_ids = tuple(
        row.geometry.source.external_event_id for row in rows if getattr(row, name) is False
    )
    unknown_ids = tuple(
        row.geometry.source.external_event_id for row in rows if getattr(row, name) is None
    )
    return MovementMetric(
        true=len(true_ids),
        false=len(false_ids),
        unknown=len(unknown_ids),
        true_event_ids=true_ids,
        false_event_ids=false_ids,
        unknown_event_ids=unknown_ids,
    )


def _summarize_type(
    rows: Sequence[MovementProgression],
    event_type: Literal["Pass", "Carry"],
) -> MovementTypeStatistics:
    selected = [row for row in rows if row.geometry.source.event_type == event_type]
    directions: dict[str, list[str]] = defaultdict(list)
    outcomes: dict[str, list[str]] = defaultdict(list)

    for row in selected:
        event_id = row.geometry.source.external_event_id
        directions[row.geometry.direction or "unknown"].append(event_id)
        if row.pass_event is not None:
            outcomes[row.pass_event.outcome].append(event_id)

    return MovementTypeStatistics(
        event_type=event_type,
        event_count=len(selected),
        external_event_ids=tuple(row.geometry.source.external_event_id for row in selected),
        geometry_unavailable_event_ids=tuple(
            row.geometry.source.external_event_id for row in selected if row.geometry.issues
        ),
        direction_event_ids={key: tuple(ids) for key, ids in sorted(directions.items())},
        pass_outcome_event_ids={key: tuple(ids) for key, ids in sorted(outcomes.items())},
        metrics={name: _metric(selected, name) for name in METRICS},
    )


def _summarize(rows: Sequence[MovementProgression]) -> MovementStatistics:
    return MovementStatistics(
        passes=_summarize_type(rows, "Pass"),
        carries=_summarize_type(rows, "Carry"),
    )


def build_movement_statistics(
    spatial: Sequence[MatchEventSpatial],
    passes: Sequence[MatchPass],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None = None,
) -> MovementStatisticsResult:
    if provider != "statsbomb":
        raise ValueError("Movement statistics require StatsBomb")

    # Reuse the existing period, identity and duplicate validation.
    select_offensive_periods(
        passes,
        available_periods=available_periods,
        periods=periods,
    )
    if any(not isinstance(pass_event, MatchPass) for pass_event in passes):
        raise ValueError("Pass input must contain normalized passes")

    selected_periods = tuple(sorted(available_periods if periods is None else periods))
    ordered = sorted(spatial, key=lambda item: item.source_index)
    spatial_by_id: dict[str, MatchEventSpatial] = {}
    seen_indexes: set[int] = set()

    for spatial_event in ordered:
        if spatial_event.provider != provider or spatial_event.external_match_id != match_id:
            raise ValueError("Spatial source identity mismatch")
        if (
            spatial_event.external_event_id in spatial_by_id
            or spatial_event.source_index in seen_indexes
        ):
            raise ValueError("Duplicate spatial source event")
        if spatial_event.period not in {*available_periods, 5}:
            raise ValueError("Spatial period is incompatible with available periods")

        spatial_by_id[spatial_event.external_event_id] = spatial_event
        seen_indexes.add(spatial_event.source_index)

    passes_by_id = {pass_event.external_event_id: pass_event for pass_event in passes}
    spatial_pass_ids = {item.external_event_id for item in ordered if item.event_type == "Pass"}
    if spatial_pass_ids != set(passes_by_id):
        raise ValueError("Pass and spatial coverage mismatch")

    for pass_event in passes:
        matching_spatial = spatial_by_id[pass_event.external_event_id]
        for field in MatchEvent.model_fields:
            if getattr(pass_event, field) != getattr(matching_spatial, field):
                raise ValueError(f"Pass header mismatch: {field}")

    rows: list[MovementProgression] = []
    player_groups: dict[tuple[str, str], list[MovementProgression]] = defaultdict(list)
    team_groups: dict[str, list[MovementProgression]] = defaultdict(list)
    player_teams: dict[str, str] = {}

    for spatial_event in ordered:
        if (
            spatial_event.event_type not in {"Pass", "Carry"}
            or spatial_event.period not in selected_periods
        ):
            continue

        team_id = spatial_event.external_team_id
        player_id = spatial_event.external_player_id
        if team_id is None or player_id is None:
            raise ValueError("Movement statistics require actor team and player")
        if player_id in player_teams and player_teams[player_id] != team_id:
            raise ValueError("Movement player belongs to multiple teams")

        player_teams[player_id] = team_id
        classification = classify_movement_progression(
            spatial_event,
            pass_event=(
                passes_by_id[spatial_event.external_event_id]
                if spatial_event.event_type == "Pass"
                else None
            ),
        )
        rows.append(classification)
        player_groups[(team_id, player_id)].append(classification)
        team_groups[team_id].append(classification)

    players = tuple(
        PlayerMovementStatistics(
            **_summarize(group).model_dump(),
            external_team_id=team_id,
            external_player_id=player_id,
        )
        for (team_id, player_id), group in sorted(player_groups.items())
    )
    teams = tuple(
        TeamMovementStatistics(
            **_summarize(group).model_dump(),
            external_team_id=team_id,
            external_player_ids=tuple(
                sorted(
                    player_id
                    for player_id, player_team in player_teams.items()
                    if player_team == team_id
                )
            ),
        )
        for team_id, group in sorted(team_groups.items())
    )

    return MovementStatisticsResult(
        **_summarize(rows).model_dump(),
        external_match_id=match_id,
        events_sha256=events_sha256,
        available_periods=tuple(sorted(available_periods)),
        periods=selected_periods,
        players=players,
        teams=teams,
        excluded_shootout_movement_event_ids=tuple(
            item.external_event_id
            for item in ordered
            if item.period == 5 and item.event_type in {"Pass", "Carry"}
        ),
    )
