import pytest

from football_analytics.ingestion.match_participation import PlayerParticipation
from football_analytics.ingestion.participation import ParticipationInterval
from football_analytics.ingestion.player_scope_participation import (
    scope_player_participation,
)
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary


def make_periods():
    return [
        PeriodBoundary(
            period=1,
            duration_ms=1000,
            start_elapsed_ms=0,
            end_elapsed_ms=1000,
        ),
        PeriodBoundary(
            period=2,
            duration_ms=1000,
            start_elapsed_ms=1000,
            end_elapsed_ms=2000,
        ),
    ]


def make_player(intervals: list[tuple[int, int]]) -> PlayerParticipation:
    return PlayerParticipation(
        external_match_id="3869685",
        external_team_id="779",
        external_player_id="5503",
        is_starter=True,
        intervals=[ParticipationInterval(start_ms=start, end_ms=end) for start, end in intervals],
        elapsed_on_field_ms=(sum(end - start for start, end in intervals) if intervals else None),
    )


def test_clips_interval_crossing_period_boundary() -> None:
    player = make_player([(800, 1200)])

    first = scope_player_participation(
        [player],
        make_periods(),
        match_id="3869685",
        selected_periods=(1,),
    )[0]
    second = scope_player_participation(
        [player],
        make_periods(),
        match_id="3869685",
        selected_periods=(2,),
    )[0]

    assert first.elapsed_in_scope_ms == 200
    assert second.elapsed_in_scope_ms == 200
    assert first.intervals[0].start_ms == 800
    assert first.intervals[0].end_ms == 1000
    assert second.intervals[0].start_ms == 1000
    assert second.intervals[0].end_ms == 1200
    assert first.status == "observed_in_scope"


def test_preserves_temporary_absence() -> None:
    result = scope_player_participation(
        [make_player([(0, 400), (600, 2000)])],
        make_periods(),
        match_id="3869685",
        selected_periods=(1,),
    )[0]

    assert result.elapsed_in_scope_ms == 800
    assert len(result.intervals) == 2


def test_distinguishes_no_interval_in_scope_from_unknown() -> None:
    known = scope_player_participation(
        [make_player([(1000, 2000)])],
        make_periods(),
        match_id="3869685",
        selected_periods=(1,),
    )[0]
    unknown = scope_player_participation(
        [make_player([])],
        make_periods(),
        match_id="3869685",
        selected_periods=(1,),
    )[0]

    assert known.elapsed_in_scope_ms == 0
    assert known.has_participation_evidence is True
    assert known.status == "no_interval_in_scope"
    assert unknown.elapsed_in_scope_ms is None
    assert unknown.has_participation_evidence is False
    assert unknown.status == "unknown"


def test_full_scope_preserves_individual_duration() -> None:
    player = make_player([(0, 400), (600, 2000)])
    result = scope_player_participation(
        [player],
        make_periods(),
        match_id="3869685",
    )[0]

    assert result.periods == (1, 2)
    assert result.elapsed_in_scope_ms == player.elapsed_on_field_ms


def test_rejects_duration_inconsistent_with_intervals() -> None:
    player = make_player([(0, 1000)])
    player.elapsed_on_field_ms = 999

    with pytest.raises(ValueError, match="differs from intervals"):
        scope_player_participation(
            [player],
            make_periods(),
            match_id="3869685",
        )


def test_rejects_overlapping_intervals() -> None:
    with pytest.raises(ValueError, match="Invalid participation intervals"):
        scope_player_participation(
            [make_player([(0, 1000), (900, 1500)])],
            make_periods(),
            match_id="3869685",
        )


def test_rejects_unavailable_extra_time() -> None:
    with pytest.raises(ValueError, match="Requested periods"):
        scope_player_participation(
            [],
            make_periods(),
            match_id="3869685",
            selected_periods=(3, 4),
        )


def test_rejects_duplicate_players() -> None:
    player = make_player([(0, 1000)])

    with pytest.raises(ValueError, match="Duplicate"):
        scope_player_participation(
            [player, player],
            make_periods(),
            match_id="3869685",
        )
