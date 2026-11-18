import pytest
from test_possession_metadata import event, metadata

from football_analytics.ingestion.play_phase import build_play_phases
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary


def build(patterns: list[str | None], *, shootout: bool = False):
    contexts = [event(i + 1, period=5 if shootout else 1) for i in range(len(patterns))]
    return build_play_phases(
        contexts,
        [
            metadata(
                context,
                play_pattern=pattern,
                external_play_pattern_id=str(i + 1) if pattern else None,
            )
            for i, (context, pattern) in enumerate(zip(contexts, patterns, strict=True))
        ],
        [
            PeriodBoundary(
                period=i, duration_ms=1000, start_elapsed_ms=(i - 1) * 1000, end_elapsed_ms=i * 1000
            )
            for i in (1, 2)
        ],
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
    )


@pytest.mark.parametrize("pattern", ["regular_play", "from_counter"])
def test_open_play(pattern: str) -> None:
    result = build([pattern])
    assert result.events[0].phase == "open_play"
    assert result.fragment_phases[0].composition == "open_play"


@pytest.mark.parametrize(
    "pattern", ["from_corner", "from_free_kick", "from_throw_in", "from_goal_kick", "from_kick_off"]
)
def test_restart_context(pattern: str) -> None:
    assert build([pattern]).events[0].phase == "set_piece"


@pytest.mark.parametrize("pattern", [None, "other", "from_keeper"])
def test_ambiguous_is_unknown(pattern: str | None) -> None:
    assert build([pattern]).events[0].phase == "unknown"


def test_mixed_fragment_does_not_propagate_first_pattern() -> None:
    result = build(["from_corner", "regular_play", None])
    assert result.fragment_phases[0].composition == "mixed"
    assert result.fragment_phases[0].event_ids_by_phase == {
        "set_piece": ("event-1",),
        "open_play": ("event-2",),
        "unknown": ("event-3",),
    }
    assert tuple(item.external_event_id for item in result.events) == result.external_event_ids


def test_shootout_remains_separate() -> None:
    result = build(["regular_play"], shootout=True)
    assert result.events == ()
    assert result.excluded_shootout_event_ids == ("event-1",)


def test_select_phase_actions_and_reject_mismatched_headers() -> None:
    from football_analytics.ingestion.play_phase import select_phase_events

    classification = build(["regular_play", "from_corner", None])
    actions = [event(3), event(2), event(1)]
    assert select_phase_events(actions, classification, phase="set_piece") == (event(2),)
    assert select_phase_events(actions, classification, phase="unknown") == (event(3),)
    with pytest.raises(ValueError, match="header"):
        select_phase_events([event(1, timestamp_ms=999)], classification, phase="open_play")
    with pytest.raises(ValueError, match="coverage"):
        select_phase_events([event(4)], classification, phase="open_play")
    with pytest.raises(ValueError, match="Duplicate"):
        select_phase_events([event(1), event(1)], classification, phase="open_play")
    with pytest.raises(ValueError, match="periods"):
        select_phase_events(actions, classification, phase="open_play", periods=(5,))
