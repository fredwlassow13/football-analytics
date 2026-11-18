import pytest
from test_shootout_participation import event

from football_analytics.ingestion.match_end_presence import (
    MatchEndPresence,
)
from football_analytics.ingestion.shootout_assessment import (
    build_shootout_assessment,
)
from football_analytics.ingestion.shootout_eligibility import (
    ShootoutEligibilityContext,
)
from football_analytics.integrations.base import MatchLineup


def presence(**changes: object) -> MatchEndPresence:
    return MatchEndPresence.model_validate(
        {
            "external_match_id": "3869685",
            "external_team_id": "779",
            "external_player_id": "player-1",
            "status": "active",
            "last_transition_index": 10,
        }
        | changes
    )


def build(events=None, presences=None, contexts=None):
    return build_shootout_assessment(
        [event(1)] if events is None else events,
        [
            MatchLineup(
                provider="statsbomb",
                external_match_id="3869685",
                external_team_id="779",
                external_player_id="player-1",
            )
        ],
        [presence()] if presences is None else presences,
        (
            {
                "player-1": ShootoutEligibilityContext(
                    sent_off=False,
                    excluded_to_equalise=False,
                )
            }
            if contexts is None
            else contexts
        ),
        provider="statsbomb",
        match_id="3869685",
    )


def test_observed_eligible_participant_is_consistent() -> None:
    result = build()[0]

    assert result.roster.participation_status == "observed"
    assert result.eligibility.status == "eligible"
    assert result.status == "consistent"
    assert result.issues == ()


def test_eligibility_does_not_invent_observed_participation() -> None:
    result = build(events=[])[0]

    assert result.eligibility.status == "eligible"
    assert result.roster.participation_status == "unknown"
    assert result.status == "consistent"


def test_observed_ineligible_participant_requires_review() -> None:
    result = build(contexts={"player-1": ShootoutEligibilityContext(sent_off=True)})[0]

    assert result.roster.participation_status == "observed"
    assert result.eligibility.status == "ineligible"
    assert result.status == "review"
    assert result.issues == ("observed_participant_classified_ineligible",)


def test_observation_does_not_overwrite_unknown_eligibility() -> None:
    result = build(contexts={"player-1": ShootoutEligibilityContext()})[0]

    assert result.roster.participation_status == "observed"
    assert result.eligibility.status == "unknown"
    assert result.status == "review"
    assert "eligibility_unknown" in result.issues


def test_requires_presence_for_every_listed_player() -> None:
    with pytest.raises(ValueError, match="Presence must cover exactly"):
        build(presences=[])


def test_rejects_duplicate_presence() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        build(presences=[presence(), presence()])


@pytest.mark.parametrize(
    "changes",
    [
        {"external_match_id": "other"},
        {"external_team_id": "771"},
    ],
)
def test_rejects_conflicting_presence_identity(changes) -> None:
    with pytest.raises(ValueError, match="identity differs"):
        build(presences=[presence(**changes)])


def test_requires_explicit_context_for_every_listed_player() -> None:
    with pytest.raises(ValueError, match="Contexts must cover exactly"):
        build(contexts={})
