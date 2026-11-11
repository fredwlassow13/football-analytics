import pytest
from pydantic import ValidationError

from football_analytics.ingestion.tactical_timeline import (
    TacticalPlayerState,
    TacticalPositionEvidence,
    TacticalSegment,
)


def segment_data() -> dict[str, object]:
    return {
        "provider": "statsbomb",
        "external_match_id": "3869685",
        "external_team_id": "779",
        "start_elapsed_ms": 1000,
        "end_elapsed_ms": 5000,
        "last_recorded_formation": "433",
        "formation_source_event_id": "starting-xi-1",
        "players": (
            TacticalPlayerState(
                external_player_id="player-1",
                last_recorded_position=TacticalPositionEvidence(
                    external_position_id="23",
                    position_name="Center Forward",
                    source_event_id="tactical-shift-1",
                ),
            ),
        ),
    }


def test_preserves_time_and_separate_evidence_sources() -> None:
    segment = TacticalSegment.model_validate(segment_data())

    assert segment.duration_ms == 4000
    assert segment.player_count == 1
    assert segment.last_recorded_formation == "433"
    assert segment.formation_source_event_id == "starting-xi-1"

    position = segment.players[0].last_recorded_position
    assert position is not None
    assert position.external_position_id == "23"
    assert position.position_name == "Center Forward"
    assert position.source_event_id == "tactical-shift-1"


def test_allows_player_without_recorded_position() -> None:
    data = segment_data()
    data["players"] = (TacticalPlayerState(external_player_id="incoming-player"),)

    segment = TacticalSegment.model_validate(data)

    assert segment.player_count == 1
    assert segment.players[0].last_recorded_position is None


def test_does_not_infer_formation_from_player_count() -> None:
    data = segment_data()
    data["players"] = tuple(
        TacticalPlayerState(external_player_id=str(number)) for number in range(1, 11)
    )

    segment = TacticalSegment.model_validate(data)

    assert segment.player_count == 10
    assert segment.last_recorded_formation == "433"


def test_rejects_duplicate_players() -> None:
    data = segment_data()
    player = TacticalPlayerState(external_player_id="player-1")
    data["players"] = (player, player)

    with pytest.raises(ValidationError, match="duplicate players"):
        TacticalSegment.model_validate(data)


def test_rejects_more_than_eleven_players() -> None:
    data = segment_data()
    data["players"] = tuple(
        TacticalPlayerState(external_player_id=str(number)) for number in range(1, 13)
    )

    with pytest.raises(ValidationError):
        TacticalSegment.model_validate(data)


def test_rejects_end_before_start() -> None:
    data = segment_data()
    data["end_elapsed_ms"] = 999

    with pytest.raises(ValidationError, match="must not precede"):
        TacticalSegment.model_validate(data)


@pytest.mark.parametrize("field", ["start_elapsed_ms", "end_elapsed_ms"])
def test_rejects_negative_time(field: str) -> None:
    data = segment_data()
    data[field] = -1

    with pytest.raises(ValidationError):
        TacticalSegment.model_validate(data)


def test_accepts_zero_duration_boundary() -> None:
    data = segment_data()
    data["end_elapsed_ms"] = 1000

    segment = TacticalSegment.model_validate(data)

    assert segment.duration_ms == 0


@pytest.mark.parametrize(
    "field",
    [
        "provider",
        "external_match_id",
        "external_team_id",
        "last_recorded_formation",
        "formation_source_event_id",
    ],
)
def test_rejects_empty_identifiers(field: str) -> None:
    data = segment_data()
    data[field] = ""

    with pytest.raises(ValidationError):
        TacticalSegment.model_validate(data)


def test_segment_is_immutable() -> None:
    segment = TacticalSegment.model_validate(segment_data())

    with pytest.raises(ValidationError, match="frozen"):
        segment.last_recorded_formation = "442"
