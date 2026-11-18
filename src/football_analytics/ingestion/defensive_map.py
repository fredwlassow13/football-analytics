"""Spatial views of the defensive summary's unique source events."""

from collections import Counter
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.defensive_scope import DefensiveEvent
from football_analytics.ingestion.defensive_summary import build_defensive_summary
from football_analytics.ingestion.player_activity import ActivityLocationIssue
from football_analytics.ingestion.player_pass_heatmap import HeatmapCell
from football_analytics.integrations.base import MatchAerialDuel, MatchEvent, MatchEventSpatial

type DefensiveMapGroup = Literal[
    "all", "recoveries", "duels", "pressure", "blocks_clearances", "fouls", "aerials"
]

DEFENSIVE_MAP_GROUPS: dict[str, frozenset[str]] = {
    "recoveries": frozenset({"Interception", "Ball Recovery"}),
    "duels": frozenset({"Duel"}),
    "pressure": frozenset({"Pressure", "Dribbled Past"}),
    "blocks_clearances": frozenset({"Block", "Clearance"}),
    "fouls": frozenset({"Foul Committed", "Foul Won"}),
}
DEFENSIVE_MAP_TYPES = frozenset().union(
    *DEFENSIVE_MAP_GROUPS.values(), {"Pass", "Shot", "Miscontrol"}
)


class DefensiveMap(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: Literal["1.0.0"] = "1.0.0"
    population_basis: Literal["unique_defensive_and_aerial_sources"] = (
        "unique_defensive_and_aerial_sources"
    )
    coordinate_system: Literal["statsbomb_120_80"] = "statsbomb_120_80"
    external_team_id: str
    external_player_id: str | None
    available_periods: tuple[int, ...]
    periods: tuple[int, ...]
    group: DefensiveMapGroup
    event_types: tuple[str, ...] | None
    candidate_event_ids: tuple[str, ...]
    candidate_counts_by_type: dict[str, int]
    points: tuple[MatchEventSpatial, ...]
    location_issues: tuple[ActivityLocationIssue, ...]
    columns: int
    rows: int
    cells: tuple[HeatmapCell, ...]

    @property
    def located_count(self) -> int:
        return len(self.points)

    @property
    def candidate_count(self) -> int:
        return len(self.candidate_event_ids)

    @property
    def percentages(self) -> tuple[float, ...] | None:
        if not self.located_count:
            return None
        return tuple(100 * cell.count / self.located_count for cell in self.cells)


def defensive_grid(
    points: Sequence[MatchEventSpatial], *, columns: int, rows: int
) -> tuple[HeatmapCell, ...]:
    if (
        type(columns) is not int
        or type(rows) is not int
        or not 1 <= columns <= 120
        or not 1 <= rows <= 80
    ):
        raise ValueError("Invalid grid dimensions")
    samples: dict[tuple[int, int], list[str]] = {
        (row, column): [] for row in range(rows) for column in range(columns)
    }
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    for point in sorted(points, key=lambda item: item.source_index):
        if point.external_event_id in seen_ids or point.source_index in seen_indexes:
            raise ValueError("Duplicate map point")
        seen_ids.add(point.external_event_id)
        seen_indexes.add(point.source_index)
        location = point.start_location
        if location is None or not 0 <= location.x <= 120 or not 0 <= location.y <= 80:
            raise ValueError("Map point requires an in-pitch location")
        column = min(int(location.x * columns / 120), columns - 1)
        row = min(int(location.y * rows / 80), rows - 1)
        samples[(row, column)].append(point.external_event_id)
    return tuple(
        HeatmapCell(row=row, column=column, external_event_ids=tuple(ids))
        for (row, column), ids in samples.items()
    )


def build_defensive_map(
    actions: Sequence[DefensiveEvent],
    aerials: Sequence[MatchAerialDuel],
    spatial: Sequence[MatchEventSpatial],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
    team_id: str,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None = None,
    player_id: str | None = None,
    group: DefensiveMapGroup = "all",
    event_types: tuple[str, ...] | None = None,
    columns: int = 12,
    rows: int = 8,
) -> DefensiveMap:
    if not team_id or player_id == "":
        raise ValueError("Team and optional player identifiers must be nonempty")
    if group not in {"all", "aerials", *DEFENSIVE_MAP_GROUPS}:
        raise ValueError("Unsupported defensive map group")
    if event_types is not None and (
        not event_types
        or len(event_types) != len(set(event_types))
        or not set(event_types).issubset(DEFENSIVE_MAP_TYPES)
    ):
        raise ValueError("Invalid defensive map event types")
    summary = build_defensive_summary(
        actions,
        aerials,
        provider=provider,
        match_id=match_id,
        events_sha256=events_sha256,
        available_periods=available_periods,
        periods=periods,
    )
    sources: dict[str, MatchEvent] = {item.external_event_id: item for item in [*actions, *aerials]}
    by_id: dict[str, MatchEventSpatial] = {}
    indexes: set[int] = set()
    for point in spatial:
        if not isinstance(point, MatchEventSpatial):
            raise ValueError("Unsupported spatial event")
        if (point.provider, point.external_match_id) != (provider, match_id):
            raise ValueError("Spatial source belongs to another match")
        if point.period not in {*available_periods, 5}:
            raise ValueError("Spatial period is incompatible with match")
        if point.external_event_id in by_id or point.source_index in indexes:
            raise ValueError("Duplicate spatial source")
        by_id[point.external_event_id] = point
        indexes.add(point.source_index)
    for key, source in sources.items():
        aligned = by_id.get(key)
        if aligned is None:
            raise ValueError("Missing defensive spatial source")
        if any(
            getattr(aligned, field) != getattr(source, field) for field in MatchEvent.model_fields
        ):
            raise ValueError("Defensive spatial source headers differ")
    if player_id is not None and any(
        item.external_player_id == player_id and item.external_team_id != team_id
        for item in sources.values()
    ):
        raise ValueError("Player source belongs to another team")
    aerial_ids = set(summary.aerial_event_ids)
    candidates: list[MatchEventSpatial] = []
    points: list[MatchEventSpatial] = []
    issues: list[ActivityLocationIssue] = []
    for key in summary.external_event_ids:
        point = by_id[key]
        if point.external_team_id != team_id or (
            player_id is not None and point.external_player_id != player_id
        ):
            continue
        if group == "aerials" and key not in aerial_ids:
            continue
        if group in DEFENSIVE_MAP_GROUPS and point.event_type not in DEFENSIVE_MAP_GROUPS[group]:
            continue
        if event_types is not None and point.event_type not in event_types:
            continue
        candidates.append(point)
        location = point.start_location
        reason: Literal["missing_location", "outside_pitch"] | None = None
        if location is None:
            reason = "missing_location"
        elif not 0 <= location.x <= 120 or not 0 <= location.y <= 80:
            reason = "outside_pitch"
        if reason is not None:
            issues.append(
                ActivityLocationIssue(
                    external_event_id=key,
                    source_index=point.source_index,
                    event_type=point.event_type,
                    reason=reason,
                )
            )
        else:
            points.append(point)
    return DefensiveMap(
        provider=provider,
        external_match_id=match_id,
        events_sha256=events_sha256,
        external_team_id=team_id,
        external_player_id=player_id,
        available_periods=summary.available_periods,
        periods=summary.periods,
        group=group,
        event_types=tuple(sorted(event_types)) if event_types is not None else None,
        candidate_event_ids=tuple(item.external_event_id for item in candidates),
        candidate_counts_by_type=dict(
            sorted(Counter(item.event_type for item in candidates).items())
        ),
        points=tuple(points),
        location_issues=tuple(issues),
        columns=columns,
        rows=rows,
        cells=defensive_grid(points, columns=columns, rows=rows),
    )
