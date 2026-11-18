import pytest

from football_analytics.ingestion.defensive_roster import build_defensive_roster
from football_analytics.ingestion.defensive_summary import build_defensive_summary
from football_analytics.ingestion.participation import ParticipationInterval
from football_analytics.ingestion.player_scope_participation import (
    PlayerScopeParticipation,
)
from football_analytics.integrations.base import MatchLineup, MatchPressure


def make_inputs():
    event = MatchPressure(
        provider="statsbomb",
        external_match_id="3869685",
        external_event_id="pass-1",
        source_index=1,
        period=1,
        timestamp_ms=100,
        external_team_id="team-1",
        external_player_id="player-1",
    )
    summary = build_defensive_summary(
        [event],
        [],
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
    return build_defensive_roster(
        summary,
        lineups,
        participation,
        lineups_sha256="b" * 64,
        participation_events_sha256="a" * 64,
    )


def test_distinguishes_observed_zero_not_applicable_and_unknown() -> None:
    result = build(*make_inputs())
    players = {item.lineup.external_player_id: item for item in result.players}

    assert result.population_basis == "listed_players"
    assert len(players) == 4
    assert result.lineups_sha256 == "b" * 64

    observed = players["player-1"]
    assert observed.counts_basis == "observed_events"
    assert observed.counts.unique_source_events == 1
    assert observed.counts.action_events == 1
    assert observed.counts.aerial_results == 0

    zero = players["player-2"]
    assert zero.counts_basis == "zero_with_presence"
    assert zero.counts.unique_source_events == 0
    assert zero.counts.aerial_results == 0
    assert zero.counts.duel_events == 0
    assert zero.observed_summary is None

    assert players["player-3"].counts_basis == "unknown"
    assert players["player-3"].counts is None
    assert players["player-4"].counts_basis == "not_applicable"
    assert players["player-4"].counts is None

    assert (
        sum(item.counts.unique_source_events for item in result.players if item.counts is not None)
        == 1
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


def test_rejects_unlisted_defensive_player() -> None:
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


def test_rejects_different_participation_source_hash() -> None:
    summary, lineups, presence = make_inputs()
    with pytest.raises(ValueError, match="hashes differ"):
        build_defensive_roster(
            summary,
            lineups,
            presence,
            lineups_sha256="b" * 64,
            participation_events_sha256="c" * 64,
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"elapsed_in_scope_ms": -1},
        {"elapsed_in_scope_ms": 999},
        {"intervals": (ParticipationInterval(start_ms=500, end_ms=100),)},
        {
            "intervals": (
                ParticipationInterval(start_ms=0, end_ms=700),
                ParticipationInterval(start_ms=600, end_ms=900),
            )
        },
        {"has_participation_evidence": False},
    ],
)
def test_rejects_inconsistent_scoped_presence(updates) -> None:
    summary, lineups, presence = make_inputs()
    presence[0] = presence[0].model_copy(update=updates)
    with pytest.raises(ValueError):
        build(summary, lineups, presence)


def test_zero_preserves_unknown_attributes_and_has_no_observed_summary() -> None:
    result = build(*make_inputs())
    zero = next(p for p in result.players if p.counts_basis == "zero_with_presence")
    assert zero.observed_summary is None
    assert set(zero.counts.actions_by_type.values()) == {0}
    observed = next(p for p in result.players if p.counts_basis == "observed_events")
    assert observed.observed_summary.actions.actions[0].flags["counterpress"].unknown == 1
    assert observed.counts.actions_by_type["Pressure"] == 1


def test_shared_aerial_source_is_not_added_twice() -> None:
    from test_defensive_summary import aerial, header

    from football_analytics.integrations.base import MatchClearance

    summary, lineups, presence = make_inputs()
    action = MatchClearance.model_validate(
        header(1, "Clearance", external_team_id="team-1") | {"aerial_won": True}
    )
    projection = aerial(1, event_type="Clearance", external_team_id="team-1")
    summary = build_defensive_summary(
        [action],
        [projection],
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
        available_periods=(1, 2),
        periods=(1,),
    )
    counts = build(summary, lineups, presence).players[0].counts
    assert counts.unique_source_events == 1
    assert counts.action_events == 1
    assert counts.aerial_results == 1
    assert counts.shared_source_events == 1
    assert counts.aerial_won == 1


def test_aerial_only_player_has_observed_counts() -> None:
    from test_defensive_summary import aerial

    summary, lineups, presence = make_inputs()
    projection = aerial(1, external_team_id="team-1")
    summary = build_defensive_summary(
        [],
        [projection],
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
        available_periods=(1, 2),
        periods=(1,),
    )
    counts = build(summary, lineups, presence).players[0].counts
    assert counts.action_events == 0
    assert counts.unique_source_events == counts.aerial_results == 1


def test_json_roundtrip_and_sorted_roster() -> None:
    from football_analytics.ingestion.defensive_roster import DefensiveRosterSummary

    summary, lineups, presence = make_inputs()
    result = build(summary, list(reversed(lineups)), list(reversed(presence)))
    assert DefensiveRosterSummary.model_validate_json(result.model_dump_json()) == result
    assert [p.lineup.external_player_id for p in result.players] == [
        "player-1",
        "player-2",
        "player-3",
        "player-4",
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("external_match_id", "other"),
        ("provider", "other"),
        ("is_starter", False),
    ],
)
def test_rejects_conflicting_lineup_identity_or_starter(field, value) -> None:
    summary, lineups, presence = make_inputs()
    lineups[0] = lineups[0].model_copy(update={field: value})
    with pytest.raises(ValueError):
        build(summary, lineups, presence)


def test_does_not_accept_sources_dropped_from_player_summary() -> None:
    summary, lineups, presence = make_inputs()
    summary = summary.model_copy(update={"players": ()})
    with pytest.raises(ValueError, match="sources differ"):
        build(summary, lineups, presence)


def test_rejects_event_with_known_absence_in_scope() -> None:
    summary, lineups, presence = make_inputs()
    presence[0] = presence[0].model_copy(update={"elapsed_in_scope_ms": 0, "intervals": ()})
    with pytest.raises(ValueError, match="without observed participation"):
        build(summary, lineups, presence)
