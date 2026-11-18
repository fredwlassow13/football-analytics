from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.integrations.base import (
    MatchBallControlIncident,
    MatchBallRecovery,
    MatchBlock,
    MatchClearance,
    MatchDribble,
    MatchDribbledPast,
    MatchDuel,
    MatchEvent,
    MatchEventSpatial,
    MatchInterception,
    MatchPass,
    MatchPressure,
)

type BallControlAction = (
    MatchBallControlIncident
    | MatchBallRecovery
    | MatchBlock
    | MatchClearance
    | MatchDribble
    | MatchDribbledPast
    | MatchDuel
    | MatchInterception
    | MatchPass
    | MatchPressure
)

EvidenceCategory = Literal[
    "dispossessed",
    "miscontrol",
    "completed_pass",
    "non_completed_pass",
    "unknown_pass_outcome",
    "completed_dribble",
    "non_completed_dribble",
    "recovery_failure_true",
    "recovery_failure_false",
    "recovery_failure_unknown",
    "interception",
    "tackle",
    "aerial_lost",
    "pressure",
    "dribbled_past",
    "block",
    "clearance",
]
LongitudinalZone = Literal["defensive_third", "middle_third", "attacking_third"]
LateralZone = Literal["low_y", "central", "high_y"]
ZoneIssue = Literal[
    "missing_start_location",
    "start_location_not_2d",
    "start_location_outside_pitch",
]


class BallControlEvidence(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    policy_version: Literal["1.0.0"] = "1.0.0"
    zone_policy_name: Literal["statsbomb_origin_thirds_channels20_60"] = (
        "statsbomb_origin_thirds_channels20_60"
    )
    orientation: Literal["action_executing_team_positive_x"] = "action_executing_team_positive_x"
    unit_of_analysis: Literal["source_event"] = "source_event"

    source: BallControlAction
    spatial: MatchEventSpatial
    category: EvidenceCategory
    reported_outcome: str | None
    longitudinal_zone: LongitudinalZone | None
    lateral_zone: LateralZone | None
    zone_issues: tuple[ZoneIssue, ...]


def _category(action: BallControlAction) -> EvidenceCategory:
    if isinstance(action, MatchBallControlIncident):
        return "dispossessed" if action.event_type == "Dispossessed" else "miscontrol"

    if isinstance(action, MatchPass):
        if action.outcome == "unknown":
            return "unknown_pass_outcome"
        return "completed_pass" if action.outcome == "complete" else "non_completed_pass"

    if isinstance(action, MatchDribble):
        return "completed_dribble" if action.outcome == "complete" else "non_completed_dribble"

    if isinstance(action, MatchBallRecovery):
        if action.recovery_failure is True:
            return "recovery_failure_true"
        if action.recovery_failure is False:
            return "recovery_failure_false"
        return "recovery_failure_unknown"

    if isinstance(action, MatchInterception):
        return "interception"
    if isinstance(action, MatchDuel):
        return action.duel_type
    if isinstance(action, MatchPressure):
        return "pressure"
    if isinstance(action, MatchDribbledPast):
        return "dribbled_past"
    if isinstance(action, MatchBlock):
        return "block"
    if isinstance(action, MatchClearance):
        return "clearance"

    raise ValueError("Unsupported ball control action")


def classify_ball_control_evidence(
    action: BallControlAction,
    spatial: MatchEventSpatial,
) -> BallControlEvidence:
    if action.provider != "statsbomb":
        raise ValueError("Ball control zones require StatsBomb orientation")

    if action.period not in {1, 2, 3, 4}:
        raise ValueError("Ball control evidence requires a playing period")

    for field in MatchEvent.model_fields:
        if getattr(action, field) != getattr(spatial, field):
            raise ValueError(f"Spatial header mismatch: {field}")

    category = _category(action)
    reported_outcome = (
        action.outcome
        if isinstance(action, MatchPass | MatchDribble | MatchInterception | MatchDuel)
        else None
    )

    point = spatial.start_location
    issues: list[ZoneIssue] = []
    longitudinal: LongitudinalZone | None = None
    lateral: LateralZone | None = None

    if point is None:
        issues.append("missing_start_location")
    else:
        if point.z is not None:
            issues.append("start_location_not_2d")
        if not (0 <= point.x <= 120 and 0 <= point.y <= 80):
            issues.append("start_location_outside_pitch")

        if not issues:
            if point.x < 40:
                longitudinal = "defensive_third"
            elif point.x < 80:
                longitudinal = "middle_third"
            else:
                longitudinal = "attacking_third"

            if point.y < 20:
                lateral = "low_y"
            elif point.y < 60:
                lateral = "central"
            else:
                lateral = "high_y"

    return BallControlEvidence(
        source=action,
        spatial=spatial,
        category=category,
        reported_outcome=reported_outcome,
        longitudinal_zone=longitudinal,
        lateral_zone=lateral,
        zone_issues=tuple(issues),
    )
