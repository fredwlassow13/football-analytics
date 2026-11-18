from math import hypot
from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.movement_geometry import (
    MovementGeometry,
    analyze_movement_geometry,
)
from football_analytics.integrations.base import (
    MatchEvent,
    MatchEventSpatial,
    MatchPass,
)


class MovementProgression(BaseModel):
    """Project progression policy applied to recorded endpoint geometry."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    policy_version: Literal["1.0.0"] = "1.0.0"
    policy_name: Literal["project_dx10_goal_distance25"] = "project_dx10_goal_distance25"

    geometry: MovementGeometry
    pass_event: MatchPass | None

    eligible_for_count: bool | None
    geometrically_progressive: bool | None
    countable_progression: bool | None
    countable_final_third_entry: bool | None
    countable_penalty_area_entry: bool | None


def classify_movement_progression(
    event: MatchEventSpatial,
    *,
    pass_event: MatchPass | None = None,
) -> MovementProgression:
    geometry = analyze_movement_geometry(event)

    eligible: bool | None

    if event.event_type == "Pass":
        if pass_event is None:
            raise ValueError("Pass geometry requires its normalized pass")

        for field in MatchEvent.model_fields:
            if getattr(event, field) != getattr(pass_event, field):
                raise ValueError(f"Pass header mismatch: {field}")

        eligible = pass_event.is_completed
    else:
        if pass_event is not None:
            raise ValueError("Carry must not receive a normalized pass")

        # A recorded carry has no inferred pass-completion result.
        eligible = True

    if geometry.issues:
        return MovementProgression(
            geometry=geometry,
            pass_event=pass_event,
            eligible_for_count=eligible,
            geometrically_progressive=None,
            countable_progression=None,
            countable_final_third_entry=None,
            countable_penalty_area_entry=None,
        )

    start = event.start_location
    delta_x = geometry.delta_x
    reduction = geometry.goal_distance_reduction
    final_third_entry = geometry.enters_final_third
    penalty_area_entry = geometry.enters_penalty_area

    if (
        start is None
        or delta_x is None
        or reduction is None
        or final_third_entry is None
        or penalty_area_entry is None
    ):
        raise ValueError("Available geometry requires all derived values")

    initial_goal_distance = hypot(120 - start.x, 40 - start.y)

    geometrically_progressive = (
        initial_goal_distance > 0 and delta_x >= 10.0 and reduction >= initial_goal_distance * 0.25
    )

    return MovementProgression(
        geometry=geometry,
        pass_event=pass_event,
        eligible_for_count=eligible,
        geometrically_progressive=geometrically_progressive,
        countable_progression=(
            None if eligible is None else eligible and geometrically_progressive
        ),
        countable_final_third_entry=(None if eligible is None else eligible and final_third_entry),
        countable_penalty_area_entry=(
            None if eligible is None else eligible and penalty_area_entry
        ),
    )
