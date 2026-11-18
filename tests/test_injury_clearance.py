from decimal import Decimal

from test_possession_metadata import event, metadata
from test_statsbomb_passes import source_pass

from football_analytics.ingestion.attack_sequences import (
    build_attack_sequences,
)
from football_analytics.ingestion.pass_statistics import (
    aggregate_player_passes,
)
from football_analytics.ingestion.team_pass_statistics import (
    aggregate_team_passes,
)
from football_analytics.integrations.base import MatchEvent, MatchPass
from football_analytics.integrations.statsbomb.creation import (
    normalize_creation_events,
)
from football_analytics.integrations.statsbomb.passes import normalize_passes
from football_analytics.integrations.statsbomb.period_clock import PeriodBoundary


def injury_source():
    source = source_pass()
    source["pass"] = {"outcome": {"name": "Injury Clearance"}}
    return source


def test_normalizes_category_and_preserves_it_in_creation() -> None:
    source = injury_source()

    normalized = normalize_passes([source], match_id=3869685)[0]
    creation = normalize_creation_events(
        [source],
        match_id=3869685,
    )[0]

    assert normalized.outcome == "injury_clearance"
    assert normalized.is_completed is False
    assert creation.outcome == "injury_clearance"


def test_player_and_team_totals_include_category_without_losing_events() -> None:
    injury = normalize_passes(
        [injury_source()],
        match_id=3869685,
    )[0]

    complete = injury.model_copy(
        update={
            "external_event_id": "pass-2",
            "source_index": 2,
            "outcome": "complete",
        }
    )
    shootout = injury.model_copy(
        update={
            "external_event_id": "pass-3",
            "source_index": 3,
            "period": 5,
        }
    )

    passes = [injury, complete, shootout]
    player = aggregate_player_passes(passes)[0]
    team = aggregate_team_passes(passes)[0]

    for result in (player, team):
        assert result.attempted == 2
        assert result.completed == 1
        assert result.not_completed == 1
        assert result.injury_clearance == 1
        assert result.incomplete == 0
        assert result.out == 0
        assert result.unknown == 0
        assert result.completion_pct == Decimal("50.00")

    assert team.external_event_ids == ("pass-1", "pass-2")


def test_injury_clearance_stops_attack_without_general_out_flag() -> None:
    contexts = [event(1), event(2)]

    first = MatchPass.model_validate(
        contexts[0].model_dump(include=set(MatchEvent.model_fields))
        | {"outcome": "injury_clearance"}
    )
    restart = MatchPass.model_validate(
        contexts[1].model_dump(include=set(MatchEvent.model_fields))
        | {"outcome": "complete", "pass_type": "Throw-in"}
    )

    result = build_attack_sequences(
        contexts,
        [
            metadata(contexts[0], duration_seconds=0.05),
            metadata(contexts[1]),
        ],
        [first, restart],
        [],
        [],
        [
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
        ],
        provider="statsbomb",
        match_id="3869685",
        events_sha256="a" * 64,
    )

    assert result.attacks[0].end_reason == "out"
    assert result.stoppages[0].closure == "explicit_restart"
    assert result.stoppages[0].duration_ms == Decimal(50)
