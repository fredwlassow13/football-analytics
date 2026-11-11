from collections import Counter

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.player_activity import PlayerActivitySelection
from football_analytics.ingestion.player_pass_heatmap import HeatmapCell


class PlayerActivityHeatmap(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    selection: PlayerActivitySelection
    columns: int
    rows: int
    cells: tuple[HeatmapCell, ...]

    @property
    def total_events(self) -> int:
        return sum(cell.count for cell in self.cells)

    @property
    def counts_by_type(self) -> dict[str, int]:
        return dict(sorted(Counter(event.event_type for event in self.selection.events).items()))

    @property
    def percentages(self) -> tuple[float, ...] | None:
        if self.total_events == 0:
            return None
        return tuple(100.0 * cell.count / self.total_events for cell in self.cells)


def build_player_activity_heatmap(
    selection: PlayerActivitySelection,
    *,
    columns: int = 12,
    rows: int = 8,
) -> PlayerActivityHeatmap:
    if (
        isinstance(columns, bool)
        or not isinstance(columns, int)
        or not 1 <= columns <= 120
        or isinstance(rows, bool)
        or not isinstance(rows, int)
        or not 1 <= rows <= 80
    ):
        raise ValueError("Invalid grid dimensions")

    if selection.location_issues:
        raise ValueError("Activity locations require review before aggregation")

    samples: dict[tuple[int, int], list[str]] = {
        (row, column): [] for row in range(rows) for column in range(columns)
    }
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()

    for event in sorted(selection.events, key=lambda item: item.source_index):
        if (
            event.provider,
            event.external_match_id,
            event.external_team_id,
            event.external_player_id,
        ) != (
            selection.provider,
            selection.external_match_id,
            selection.external_team_id,
            selection.external_player_id,
        ):
            raise ValueError("Activity event belongs to another scope")

        if event.period not in selection.periods:
            raise ValueError("Activity event belongs to an unselected period")

        if event.external_event_id in seen_ids or event.source_index in seen_indexes:
            raise ValueError("Duplicate activity event")

        seen_ids.add(event.external_event_id)
        seen_indexes.add(event.source_index)

        location = event.start_location
        if location is None:
            raise ValueError("Activity event requires a start location")
        if not 0 <= location.x <= 120 or not 0 <= location.y <= 80:
            raise ValueError("Activity location outside pitch requires review")

        column = min(int(location.x * columns / 120), columns - 1)
        row = min(int(location.y * rows / 80), rows - 1)
        samples[(row, column)].append(event.external_event_id)

    return PlayerActivityHeatmap(
        selection=selection,
        columns=columns,
        rows=rows,
        cells=tuple(
            HeatmapCell(
                row=row,
                column=column,
                external_event_ids=tuple(samples[(row, column)]),
            )
            for row in range(rows)
            for column in range(columns)
        ),
    )
