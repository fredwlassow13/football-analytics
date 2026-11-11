from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.integrations.base import MatchEventSpatial


class HeatmapCell(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    column: int
    row: int
    external_event_ids: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.external_event_ids)


class PlayerPassHeatmap(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    external_team_id: str
    external_player_id: str
    coordinate_system: Literal["statsbomb_120_80"] = "statsbomb_120_80"
    event_type: Literal["Pass"] = "Pass"
    location_kind: Literal["start"] = "start"
    periods: tuple[int, ...]
    columns: int
    rows: int
    cells: tuple[HeatmapCell, ...]
    outside_pitch_event_ids: tuple[str, ...]

    @property
    def located_count(self) -> int:
        return sum(cell.count for cell in self.cells)

    @property
    def total_events(self) -> int:
        return self.located_count + len(self.outside_pitch_event_ids)

    @property
    def percentages(self) -> tuple[float, ...] | None:
        """Percentage of in-pitch samples in each cell."""
        if self.located_count == 0:
            return None
        return tuple(100.0 * cell.count / self.located_count for cell in self.cells)


def build_player_pass_heatmap(
    events: Sequence[MatchEventSpatial],
    *,
    provider: str,
    match_id: str,
    team_id: str,
    player_id: str,
    periods: tuple[int, ...] = (1, 2, 3, 4),
    columns: int = 12,
    rows: int = 8,
) -> PlayerPassHeatmap:
    if not all((provider, match_id, team_id, player_id)):
        raise ValueError("Provider, match, team and player are required")

    if (
        isinstance(columns, bool)
        or not isinstance(columns, int)
        or not 1 <= columns <= 120
        or isinstance(rows, bool)
        or not isinstance(rows, int)
        or not 1 <= rows <= 80
    ):
        raise ValueError("Invalid grid dimensions")

    if (
        not periods
        or len(periods) != len(set(periods))
        or any(
            isinstance(period, bool) or not isinstance(period, int) or period not in {1, 2, 3, 4}
            for period in periods
        )
    ):
        raise ValueError("Periods must be unique playing periods")

    event_ids: set[str] = set()
    indexes: set[int] = set()
    samples: dict[tuple[int, int], list[str]] = {
        (row, column): [] for row in range(rows) for column in range(columns)
    }
    outside: list[str] = []

    for event in sorted(events, key=lambda item: item.source_index):
        if (event.provider, event.external_match_id) != (provider, match_id):
            raise ValueError("Events must belong to the requested provider and match")

        if event.external_event_id in event_ids or event.source_index in indexes:
            raise ValueError("Duplicate source event")
        event_ids.add(event.external_event_id)
        indexes.add(event.source_index)

        if (
            event.event_type != "Pass"
            or event.period not in periods
            or event.external_player_id != player_id
        ):
            continue

        if event.external_team_id != team_id:
            raise ValueError("Player event belongs to another team")

        location = event.start_location
        if location is None:
            raise ValueError("Pass heatmap requires a start location")

        if not 0 <= location.x <= 120 or not 0 <= location.y <= 80:
            outside.append(event.external_event_id)
            continue

        column = min(int(location.x * columns / 120), columns - 1)
        row = min(int(location.y * rows / 80), rows - 1)
        samples[(row, column)].append(event.external_event_id)

    return PlayerPassHeatmap(
        provider=provider,
        external_match_id=match_id,
        external_team_id=team_id,
        external_player_id=player_id,
        periods=tuple(sorted(periods)),
        columns=columns,
        rows=rows,
        cells=tuple(
            HeatmapCell(
                column=column,
                row=row,
                external_event_ids=tuple(samples[(row, column)]),
            )
            for row in range(rows)
            for column in range(columns)
        ),
        outside_pitch_event_ids=tuple(outside),
    )
