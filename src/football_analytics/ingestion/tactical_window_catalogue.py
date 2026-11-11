from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BoundaryReason = Literal[
    "period_start",
    "period_end",
    "substitution",
    "dismissal",
    "tactical_shift",
]


class TacticalBoundary(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    period: int = Field(ge=1, le=4)
    elapsed_ms: int = Field(ge=0)
    source_index: int = Field(gt=0)
    reason: BoundaryReason
    source_event_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_event_provenance(self) -> "TacticalBoundary":
        if self.reason not in {"period_start", "period_end"} and self.source_event_id is None:
            raise ValueError("Tactical change requires a source event ID")

        return self


class TacticalWindow(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    external_match_id: str = Field(min_length=1)
    period: int = Field(ge=1, le=4)
    number_in_period: int = Field(gt=0)
    start_elapsed_ms: int = Field(ge=0)
    end_elapsed_ms: int = Field(ge=0)
    start_source_index: int = Field(gt=0)
    end_source_index: int = Field(gt=0)
    start_reasons: tuple[BoundaryReason, ...]
    start_event_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_bounds(self) -> "TacticalWindow":
        if self.end_elapsed_ms < self.start_elapsed_ms:
            raise ValueError("Window clock moves backwards")

        if self.end_source_index <= self.start_source_index:
            raise ValueError("Window source order must advance")

        return self

    @property
    def duration_ms(self) -> int:
        return self.end_elapsed_ms - self.start_elapsed_ms


def build_tactical_window_catalogue(
    boundaries: list[TacticalBoundary],
    *,
    provider: str,
    external_match_id: str,
) -> list[TacticalWindow]:
    period_numbers = sorted({boundary.period for boundary in boundaries})

    if period_numbers not in ([1, 2], [1, 2, 3, 4]):
        raise ValueError("Expected two periods or four with extra time")

    result: list[TacticalWindow] = []
    previous_end_ms = 0
    previous_end_index = 0

    for period in period_numbers:
        period_boundaries = [boundary for boundary in boundaries if boundary.period == period]
        starts = [boundary for boundary in period_boundaries if boundary.reason == "period_start"]
        ends = [boundary for boundary in period_boundaries if boundary.reason == "period_end"]

        if len(starts) != 1 or len(ends) != 1:
            raise ValueError("Each period requires exactly one start and end")

        start = starts[0]
        end = ends[0]

        if start.elapsed_ms != previous_end_ms:
            raise ValueError("Periods must be contiguous in elapsed time")

        if (
            end.elapsed_ms <= start.elapsed_ms
            or end.source_index <= start.source_index
            or start.source_index < previous_end_index
        ):
            raise ValueError("Invalid period duration or source order")

        grouped: dict[int, list[TacticalBoundary]] = defaultdict(list)

        for boundary in period_boundaries:
            if not (
                start.elapsed_ms <= boundary.elapsed_ms <= end.elapsed_ms
                and start.source_index <= boundary.source_index <= end.source_index
            ):
                raise ValueError("Boundary outside period bounds")

            grouped[boundary.source_index].append(boundary)

        times: dict[int, int] = {}

        for source_index, group in grouped.items():
            elapsed_values = {boundary.elapsed_ms for boundary in group}

            if len(elapsed_values) != 1:
                raise ValueError("Conflicting clocks for the same source index")

            times[source_index] = next(iter(elapsed_values))

        indexes = sorted(grouped)

        for number, (left, right) in enumerate(
            zip(indexes, indexes[1:], strict=False),
            start=1,
        ):
            group = grouped[left]

            result.append(
                TacticalWindow(
                    provider=provider,
                    external_match_id=external_match_id,
                    period=period,
                    number_in_period=number,
                    start_elapsed_ms=times[left],
                    end_elapsed_ms=times[right],
                    start_source_index=left,
                    end_source_index=right,
                    start_reasons=tuple(sorted({boundary.reason for boundary in group})),
                    start_event_ids=tuple(
                        sorted(
                            {
                                boundary.source_event_id
                                for boundary in group
                                if boundary.source_event_id is not None
                            }
                        )
                    ),
                )
            )

        previous_end_ms = end.elapsed_ms
        previous_end_index = end.source_index

    return result
