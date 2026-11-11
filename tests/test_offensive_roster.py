import pytest

from football_analytics.ingestion.offensive_roster import build_offensive_roster
from football_analytics.ingestion.offensive_summary import build_offensive_summary
from football_analytics.ingestion.participation import ParticipationInterval
from football_analytics.ingestion.player_scope_participation import (
    PlayerScopeParticipation,
)
from football_analytics.integrations.base import MatchLineup, MatchPass


def make_inputs():
    event = MatchPass(
        provider="statsbomb",
        external_match_id="3869685",
        external_event_id="pass-1",
        source_index=1,
        period=1,
        timestamp_ms=100,
        external_team_id="team-1",
        external_player_id="player-1",
        outcome="complete",
    )
    summary = build_offensive_summary(
        [event],
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
        available_periods=(1, 2),
        periods=(1,),
    )
    lineups = []
    participation = []

    for number in range(1, 5):
        player_id = f"player-{number}"
        team_id = "team-2" if number == 4 else "team-1"
        active = number in {1, 2}
        known = number != 3

        lineups.append(
            MatchLineup(
                provider="statsbomb",
                external_match_id="3869685",
                external_team_id=team_id,
                external_player_id=player_id,
                shirt_number=number,
                is_starter=active,
            )
        )
        participation.append(
            PlayerScopeParticipation(
                external_match_id="3869685",
                external_team_id=team_id,
                external_player_id=player_id,
                is_starter=active,
                periods=(1,),
                has_participation_evidence=known,
                elapsed_in_scope_ms=1000 if active else 0 if known else None,
                intervals=((ParticipationInterval(start_ms=0, end_ms=1000),) if active else ()),
            )
        )

    return summary, lineups, participation


def build(summary, lineups, participation):
    return build_offensive_roster(
        summary,
        lineups,
        participation,
        lineups_sha256="b" * 64,
    )


def test_distinguishes_observed_zero_not_applicable_and_unknown() -> None:
    result = build(*make_inputs())
    players = {item.lineup.external_player_id: item for item in result.players}

    assert result.population_basis == "listed_players"
    assert len(players) == 4
    assert result.lineups_sha256 == "b" * 64

    observed = players["player-1"]
    assert observed.counts_basis == "observed_events"
    assert observed.counts.passes_attempted == 1
    assert observed.counts.passes_completed == 1
    assert observed.counts.shots == 0

    zero = players["player-2"]
    assert zero.counts_basis == "zero_with_presence"
    assert zero.counts.passes_attempted == 0
    assert zero.counts.shots == 0
    assert zero.counts.dribbles_attempted == 0
    assert zero.observed_summary is None

    assert players["player-3"].counts_basis == "unknown"
    assert players["player-3"].counts is None
    assert players["player-4"].counts_basis == "not_applicable"
    assert players["player-4"].counts is None

    assert (
        sum(item.counts.passes_attempted for item in result.players if item.counts is not None) == 1
    )


@pytest.mark.parametrize("duplicate", ["lineup", "participation"])
def test_rejects_duplicate_players(duplicate: str) -> None:
    summary, lineups, participation = make_inputs()
    if duplicate == "lineup":
        lineups.append(lineups[0])
    else:
        participation.append(participation[0])

    with pytest.raises(ValueError, match="Duplicate"):
        build(summary, lineups, participation)


def test_requires_participation_row_for_every_listed_player() -> None:
    summary, lineups, participation = make_inputs()

    with pytest.raises(ValueError, match="exactly the listed"):
        build(summary, lineups, participation[:-1])


def test_rejects_different_period_scope() -> None:
    summary, lineups, participation = make_inputs()
    participation[0] = participation[0].model_copy(update={"periods": (2,)})

    with pytest.raises(ValueError, match="same periods"):
        build(summary, lineups, participation)


def test_rejects_unlisted_offensive_player() -> None:
    summary, lineups, participation = make_inputs()

    with pytest.raises(ValueError, match="unlisted"):
        build(summary, lineups[1:], participation[1:])


def test_requires_review_for_events_without_observed_presence() -> None:
    summary, lineups, participation = make_inputs()
    participation[0] = participation[0].model_copy(
        update={
            "has_participation_evidence": False,
            "elapsed_in_scope_ms": None,
            "intervals": (),
        }
    )

    with pytest.raises(ValueError, match="without observed participation"):
        build(summary, lineups, participation)


def test_rejects_participation_team_mismatch() -> None:
    summary, lineups, participation = make_inputs()
    participation[0] = participation[0].model_copy(update={"external_team_id": "team-2"})

    with pytest.raises(ValueError, match="identity differs"):
        build(summary, lineups, participation)
