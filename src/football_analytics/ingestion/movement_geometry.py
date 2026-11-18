from math import hypot
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.integrations.base import EventLocation, MatchEventSpatial

MovementDirection = Literal["forward", "backward", "lateral", "stationary"]
MovementGeometryIssue = Literal[
    "missing_start_location",
    "missing_end_location",
    "start_location_not_2d",
    "end_location_not_2d",
    "start_location_outside_pitch",
    "end_location_outside_pitch",
]


class MovementGeometry(BaseModel):
    """Endpoint geometry, independent of pass completion or possession labels."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    source: MatchEventSpatial
    policy_version: Literal["1.0.0"] = "1.0.0"
    orientation: Literal["action_executing_team_positive_x"] = "action_executing_team_positive_x"
    units: Literal["statsbomb_coordinate_units"] = "statsbomb_coordinate_units"
    issues: tuple[MovementGeometryIssue, ...] = ()

    delta_x: float | None = None
    delta_y: float | None = None
    straight_line_distance: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    goal_distance_reduction: float | None = None
    direction: MovementDirection | None = None

    starts_in_final_third: bool | None = None
    ends_in_final_third: bool | None = None
    enters_final_third: bool | None = None

    starts_in_penalty_area: bool | None = None
    ends_in_penalty_area: bool | None = None
    enters_penalty_area: bool | None = None


def _inside_pitch(point: EventLocation) -> bool:
    return 0 <= point.x <= 120 and 0 <= point.y <= 80


def _inside_penalty_area(point: EventLocation) -> bool:
    return 102 <= point.x <= 120 and 18 <= point.y <= 62


def analyze_movement_geometry(event: MatchEventSpatial) -> MovementGeometry:
    """Analyze a playing-period Pass or Carry without changing source points."""

    if event.provider != "statsbomb":
        raise ValueError("Movement geometry requires StatsBomb orientation")

    if event.event_type not in {"Pass", "Carry"}:
        raise ValueError("Movement geometry supports only Pass and Carry")

    if event.period not in {1, 2, 3, 4}:
        raise ValueError("Movement geometry requires a playing period")

    start = event.start_location
    end = event.end_location
    issues: list[MovementGeometryIssue] = []

    if start is None:
        issues.append("missing_start_location")
    else:
        if start.z is not None:
            issues.append("start_location_not_2d")
        if not _inside_pitch(start):
            issues.append("start_location_outside_pitch")

    if end is None:
        issues.append("missing_end_location")
    else:
        if end.z is not None:
            issues.append("end_location_not_2d")
        if not _inside_pitch(end):
            issues.append("end_location_outside_pitch")

    if issues:
        return MovementGeometry(source=event, issues=tuple(issues))

    # Explicit checks also narrow the optional types for static analysis.
    if start is None or end is None:
        raise ValueError("Movement locations are required")

    delta_x = end.x - start.x
    delta_y = end.y - start.y

    direction: MovementDirection
    if delta_x > 0:
        direction = "forward"
    elif delta_x < 0:
        direction = "backward"
    elif delta_y != 0:
        direction = "lateral"
    else:
        direction = "stationary"

    starts_in_final_third = start.x >= 80
    ends_in_final_third = end.x >= 80
    starts_in_penalty_area = _inside_penalty_area(start)
    ends_in_penalty_area = _inside_penalty_area(end)

    return MovementGeometry(
        source=event,
        delta_x=delta_x,
        delta_y=delta_y,
        straight_line_distance=hypot(delta_x, delta_y),
        goal_distance_reduction=(
            hypot(120 - start.x, 40 - start.y) - hypot(120 - end.x, 40 - end.y)
        ),
        direction=direction,
        starts_in_final_third=starts_in_final_third,
        ends_in_final_third=ends_in_final_third,
        enters_final_third=(not starts_in_final_third and ends_in_final_third),
        starts_in_penalty_area=starts_in_penalty_area,
        ends_in_penalty_area=ends_in_penalty_area,
        enters_penalty_area=(not starts_in_penalty_area and ends_in_penalty_area),
    )
