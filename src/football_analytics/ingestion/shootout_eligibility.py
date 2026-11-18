from typing import Literal

from pydantic import BaseModel, ConfigDict

from football_analytics.ingestion.match_end_presence import (
    MatchEndPresence,
)


class ShootoutEligibilityContext(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    sent_off: bool | None = None
    excluded_to_equalise: bool | None = None
    temporary_absence_confirmed: bool | None = None
    goalkeeper_replacement_authorized: bool | None = None
    last_exit_reason: str | None = None


class ShootoutEligibilityAssessment(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    external_match_id: str
    external_team_id: str
    external_player_id: str
    status: Literal["eligible", "ineligible", "unknown"]
    reason: str
    policy_version: Literal["1.0.0"] = "1.0.0"
    issues: tuple[str, ...] = ()


def assess_initial_shootout_eligibility(
    presence: MatchEndPresence,
    context: ShootoutEligibilityContext,
) -> ShootoutEligibilityAssessment:
    """Assess eligibility before the shootout, from explicit evidence only."""
    status: Literal["eligible", "ineligible", "unknown"]
    reason: str

    recorded_red_card = context.last_exit_reason in {
        "Red Card",
        "Second Yellow",
    }
    issues: list[str] = []

    if recorded_red_card and context.sent_off is False:
        issues.append("red_card_conflicts_with_sent_off_false")

    if presence.status == "active" and context.last_exit_reason is not None:
        issues.append("active_presence_with_current_exit_reason")

    if context.temporary_absence_confirmed is True and context.last_exit_reason in {
        "Substitution",
        "Red Card",
        "Second Yellow",
    }:
        issues.append("temporary_absence_with_terminal_exit")

    if presence.status == "unknown" and context.last_exit_reason is not None:
        issues.append("exit_reason_without_reconstructed_presence")

    if issues:
        return ShootoutEligibilityAssessment(
            external_match_id=presence.external_match_id,
            external_team_id=presence.external_team_id,
            external_player_id=presence.external_player_id,
            status="unknown",
            reason="conflicting_evidence",
            issues=tuple(issues),
        )

    if context.sent_off is True or recorded_red_card:
        status = "ineligible"
        reason = "sent_off"
    elif context.sent_off is None:
        status = "unknown"
        reason = "disciplinary_coverage_unknown"
    elif context.goalkeeper_replacement_authorized is True:
        status = "eligible"
        reason = "authorized_goalkeeper_replacement"
    elif context.excluded_to_equalise is True:
        status = "ineligible"
        reason = "excluded_to_equalise"
    elif context.excluded_to_equalise is None:
        status = "unknown"
        reason = "equalisation_coverage_unknown"
    elif presence.status == "active":
        status = "eligible"
        reason = "active_at_match_end"
    elif context.last_exit_reason == "Substitution":
        status = "ineligible"
        reason = "substituted_before_shootout"
    elif presence.status == "inactive" and context.temporary_absence_confirmed is True:
        status = "eligible"
        reason = "confirmed_temporary_absence"
    else:
        status = "unknown"
        reason = "insufficient_match_end_evidence"

    return ShootoutEligibilityAssessment(
        external_match_id=presence.external_match_id,
        external_team_id=presence.external_team_id,
        external_player_id=presence.external_player_id,
        status=status,
        reason=reason,
    )
