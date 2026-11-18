import pytest

from football_analytics.ingestion.match_end_presence import (
    MatchEndPresence,
)
from football_analytics.ingestion.shootout_coverage import (
    ShootoutCoverageAudit,
    apply_shootout_coverage_audit,
)
from football_analytics.ingestion.shootout_eligibility import (
    ShootoutEligibilityContext,
)
from football_analytics.ingestion.shootout_inputs import (
    ShootoutPreparedPlayer,
)


def prepared(**changes):
    return ShootoutPreparedPlayer(
        presence=MatchEndPresence(
            external_match_id="3869685",
            external_team_id="779",
            external_player_id="player-1",
            status="active",
            last_transition_index=10,
        ),
        eligibility_context=ShootoutEligibilityContext.model_validate(changes),
    )


def audit(**changes):
    return ShootoutCoverageAudit.model_validate(
        {
            "events_sha256": "a" * 64,
            "lineups_sha256": "b" * 64,
        }
        | changes
    )


def apply(items, review):
    return apply_shootout_coverage_audit(
        items,
        review,
        events_sha256="a" * 64,
        lineups_sha256="b" * 64,
    )


def test_unreviewed_coverage_remains_unknown() -> None:
    result = apply([prepared()], audit())[0]

    assert result.eligibility_context.sent_off is None
    assert result.eligibility_context.excluded_to_equalise is None


def test_reviewed_empty_lists_produce_explicit_negative_flags() -> None:
    result = apply(
        [prepared()],
        audit(sent_off_player_ids=(), excluded_player_ids=()),
    )[0]

    assert result.eligibility_context.sent_off is False
    assert result.eligibility_context.excluded_to_equalise is False


def test_reviewed_player_is_marked_explicitly() -> None:
    result = apply(
        [prepared()],
        audit(
            sent_off_player_ids=("player-1",),
            excluded_player_ids=("player-1",),
        ),
    )[0]

    assert result.eligibility_context.sent_off is True
    assert result.eligibility_context.excluded_to_equalise is True


@pytest.mark.parametrize(
    "changes",
    [
        {"events_sha256": "c" * 64},
        {"lineups_sha256": "c" * 64},
    ],
)
def test_rejects_audit_from_different_sources(changes) -> None:
    with pytest.raises(ValueError, match="hashes differ"):
        apply([prepared()], audit(**changes))


def test_rejects_unlisted_audited_player() -> None:
    with pytest.raises(ValueError, match="unlisted player"):
        apply(
            [prepared()],
            audit(sent_off_player_ids=("other-player",)),
        )


def test_review_cannot_erase_recorded_sending_off() -> None:
    with pytest.raises(ValueError, match="contradicts"):
        apply(
            [prepared(sent_off=True)],
            audit(sent_off_player_ids=()),
        )
