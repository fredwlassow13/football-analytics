import pytest

from football_analytics.ingestion.match_end_presence import (
    MatchEndPresence,
)
from football_analytics.ingestion.shootout_eligibility import (
    ShootoutEligibilityContext,
    assess_initial_shootout_eligibility,
)


def assess(status: str = "active", **changes: object):
    presence = MatchEndPresence.model_validate(
        {
            "external_match_id": "3869685",
            "external_team_id": "779",
            "external_player_id": "player-1",
            "status": status,
            "last_transition_index": (None if status == "unknown" else 10),
        }
    )
    context = ShootoutEligibilityContext.model_validate(changes)

    return assess_initial_shootout_eligibility(presence, context)


def test_active_player_with_required_coverage_is_eligible() -> None:
    result = assess(sent_off=False, excluded_to_equalise=False)

    assert result.status == "eligible"
    assert result.reason == "active_at_match_end"


def test_active_player_without_disciplinary_coverage_is_unknown() -> None:
    result = assess()

    assert result.status == "unknown"
    assert result.reason == "disciplinary_coverage_unknown"


def test_missing_equalisation_coverage_remains_unknown() -> None:
    result = assess(sent_off=False)

    assert result.status == "unknown"
    assert result.reason == "equalisation_coverage_unknown"


def test_explicit_exclusion_is_ineligible() -> None:
    result = assess(sent_off=False, excluded_to_equalise=True)

    assert result.status == "ineligible"
    assert result.reason == "excluded_to_equalise"


@pytest.mark.parametrize("reason", ["Red Card", "Second Yellow"])
def test_recorded_red_card_is_ineligible(reason: str) -> None:
    result = assess("inactive", last_exit_reason=reason)

    assert result.status == "ineligible"
    assert result.reason == "sent_off"


def test_substituted_player_is_ineligible_without_replacement_evidence() -> None:
    result = assess(
        "inactive",
        sent_off=False,
        excluded_to_equalise=False,
        last_exit_reason="Substitution",
    )

    assert result.status == "ineligible"
    assert result.reason == "substituted_before_shootout"


def test_confirmed_temporary_absence_can_be_eligible() -> None:
    result = assess(
        "inactive",
        sent_off=False,
        excluded_to_equalise=False,
        last_exit_reason="Player Off",
        temporary_absence_confirmed=True,
    )

    assert result.status == "eligible"
    assert result.reason == "confirmed_temporary_absence"


def test_player_off_alone_does_not_prove_temporary_absence() -> None:
    result = assess(
        "inactive",
        sent_off=False,
        excluded_to_equalise=False,
        last_exit_reason="Player Off",
    )

    assert result.status == "unknown"


def test_authorized_goalkeeper_replacement_is_explicit_exception() -> None:
    result = assess(
        "unknown",
        sent_off=False,
        excluded_to_equalise=True,
        goalkeeper_replacement_authorized=True,
    )

    assert result.status == "eligible"
    assert result.reason == "authorized_goalkeeper_replacement"


def test_authorization_does_not_override_sending_off() -> None:
    result = assess(
        "inactive",
        sent_off=True,
        goalkeeper_replacement_authorized=True,
    )

    assert result.status == "ineligible"
    assert result.reason == "sent_off"


def test_unknown_presence_is_not_automatically_ineligible() -> None:
    result = assess(
        "unknown",
        sent_off=False,
        excluded_to_equalise=False,
    )

    assert result.status == "unknown"
    assert result.reason == "insufficient_match_end_evidence"


def test_red_card_and_explicit_no_sending_off_require_review() -> None:
    result = assess(
        "inactive",
        sent_off=False,
        last_exit_reason="Red Card",
    )

    assert result.status == "unknown"
    assert result.reason == "conflicting_evidence"
    assert result.issues == ("red_card_conflicts_with_sent_off_false",)


def test_active_presence_with_current_exit_reason_requires_review() -> None:
    result = assess(
        sent_off=False,
        excluded_to_equalise=False,
        last_exit_reason="Substitution",
    )

    assert result.status == "unknown"
    assert result.issues == ("active_presence_with_current_exit_reason",)


def test_temporary_absence_cannot_override_substitution() -> None:
    result = assess(
        "inactive",
        sent_off=False,
        excluded_to_equalise=False,
        last_exit_reason="Substitution",
        temporary_absence_confirmed=True,
    )

    assert result.status == "unknown"
    assert result.issues == ("temporary_absence_with_terminal_exit",)


def test_exit_reason_requires_reconstructed_presence() -> None:
    result = assess(
        "unknown",
        sent_off=False,
        excluded_to_equalise=False,
        last_exit_reason="Player Off",
    )

    assert result.status == "unknown"
    assert result.issues == ("exit_reason_without_reconstructed_presence",)


def test_consistent_evidence_has_no_issues() -> None:
    result = assess(
        sent_off=False,
        excluded_to_equalise=False,
    )

    assert result.status == "eligible"
    assert result.issues == ()
