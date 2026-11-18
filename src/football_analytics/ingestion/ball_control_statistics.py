from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.ball_control_evidence import (
    BallControlAction,
    BallControlEvidence,
    EvidenceCategory,
    LateralZone,
    LongitudinalZone,
    classify_ball_control_evidence,
)
from football_analytics.integrations.base import (
    MatchEvent,
    MatchEventPressureMetadata,
    MatchEventSpatial,
)

CONTROL_EVENT_TYPES = frozenset(
    {
        "Dispossessed",
        "Miscontrol",
        "Dribble",
        "Pass",
        "Ball Recovery",
        "Interception",
        "Duel",
        "Pressure",
        "Dribbled Past",
        "Block",
        "Clearance",
    }
)

type ControlRow = tuple[BallControlEvidence, MatchEventPressureMetadata]
type CellKey = tuple[
    str,
    EvidenceCategory,
    str | None,
    LongitudinalZone | None,
    LateralZone | None,
    bool | None,
    bool | None,
]


class ControlFlagCoverage(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    true: int = Field(ge=0)
    false: int = Field(ge=0)
    unknown: int = Field(ge=0)


class BallControlCell(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    event_type: str
    category: EvidenceCategory
    reported_outcome: str | None
    longitudinal_zone: LongitudinalZone | None
    lateral_zone: LateralZone | None
    under_pressure: bool | None
    counterpress: bool | None
    event_count: int = Field(ge=0)
    external_event_ids: tuple[str, ...]


class BallControlStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    event_count: int = Field(ge=0)
    external_event_ids: tuple[str, ...]
    zone_unavailable_event_ids: tuple[str, ...]
    under_pressure: ControlFlagCoverage
    counterpress: ControlFlagCoverage
    cells: tuple[BallControlCell, ...]


class PlayerBallControlStatistics(BallControlStatistics):
    external_team_id: str
    external_player_id: str


class TeamBallControlStatistics(BallControlStatistics):
    external_team_id: str
    external_player_ids: tuple[str, ...]


class BallControlStatisticsResult(BallControlStatistics):
    provider: Literal["statsbomb"] = "statsbomb"
    external_match_id: str
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    formula_version: Literal["1.0.0"] = "1.0.0"
    evidence_policy_version: Literal["1.0.0"] = "1.0.0"
    zone_policy_name: Literal["statsbomb_origin_thirds_channels20_60"] = (
        "statsbomb_origin_thirds_channels20_60"
    )
    population_basis: Literal["observed_control_actions"] = "observed_control_actions"
    unit_of_analysis: Literal["source_event"] = "source_event"
    available_periods: tuple[int, ...]
    periods: tuple[int, ...]
    players: tuple[PlayerBallControlStatistics, ...]
    teams: tuple[TeamBallControlStatistics, ...]
    excluded_shootout_action_event_ids: tuple[str, ...]


def _select_periods(
    available: tuple[int, ...],
    requested: tuple[int, ...] | None,
) -> tuple[int, ...]:
    if (
        any(type(period) is not int for period in available)
        or len(available) != len(set(available))
        or set(available) not in ({1, 2}, {1, 2, 3, 4})
    ):
        raise ValueError("Available periods must describe a complete match")

    selected = available if requested is None else requested
    if (
        not selected
        or any(type(period) is not int for period in selected)
        or len(selected) != len(set(selected))
        or not set(selected).issubset(available)
    ):
        raise ValueError("Requested periods are invalid or unavailable")

    return tuple(sorted(selected))


def _index_source[T: MatchEvent](
    events: Sequence[T],
    *,
    provider: str,
    match_id: str,
    available_periods: tuple[int, ...],
) -> dict[str, T]:
    by_id: dict[str, T] = {}
    indexes: set[int] = set()

    for event in events:
        if event.provider != provider or event.external_match_id != match_id:
            raise ValueError("Source identity mismatch")
        if event.external_event_id in by_id or event.source_index in indexes:
            raise ValueError("Duplicate source event")
        if event.period not in {*available_periods, 5}:
            raise ValueError("Source period is incompatible with available periods")

        by_id[event.external_event_id] = event
        indexes.add(event.source_index)

    return by_id


def _flag_coverage(
    rows: Sequence[ControlRow],
    field: Literal["under_pressure", "counterpress"],
) -> ControlFlagCoverage:
    values = [getattr(metadata, field) for _, metadata in rows]
    return ControlFlagCoverage(
        true=sum(value is True for value in values),
        false=sum(value is False for value in values),
        unknown=sum(value is None for value in values),
    )


def _summarize(rows: Sequence[ControlRow]) -> BallControlStatistics:
    groups: dict[CellKey, list[str]] = defaultdict(list)

    for evidence, metadata in rows:
        key: CellKey = (
            evidence.source.event_type,
            evidence.category,
            evidence.reported_outcome,
            evidence.longitudinal_zone,
            evidence.lateral_zone,
            metadata.under_pressure,
            metadata.counterpress,
        )
        groups[key].append(evidence.source.external_event_id)

    cells = tuple(
        BallControlCell(
            event_type=key[0],
            category=key[1],
            reported_outcome=key[2],
            longitudinal_zone=key[3],
            lateral_zone=key[4],
            under_pressure=key[5],
            counterpress=key[6],
            event_count=len(ids),
            external_event_ids=tuple(ids),
        )
        for key, ids in sorted(
            groups.items(),
            key=lambda item: tuple("" if value is None else str(value) for value in item[0]),
        )
    )

    return BallControlStatistics(
        event_count=len(rows),
        external_event_ids=tuple(evidence.source.external_event_id for evidence, _ in rows),
        zone_unavailable_event_ids=tuple(
            evidence.source.external_event_id for evidence, _ in rows if evidence.zone_issues
        ),
        under_pressure=_flag_coverage(rows, "under_pressure"),
        counterpress=_flag_coverage(rows, "counterpress"),
        cells=cells,
    )


def build_ball_control_statistics(
    actions: Sequence[BallControlAction],
    spatial: Sequence[MatchEventSpatial],
    pressure_metadata: Sequence[MatchEventPressureMetadata],
    *,
    provider: str,
    match_id: str,
    events_sha256: str,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None = None,
) -> BallControlStatisticsResult:
    if provider != "statsbomb":
        raise ValueError("Ball control statistics require StatsBomb")

    selected_periods = _select_periods(available_periods, periods)

    # Keep arguments explicit for static type checking.
    spatial_by_id = _index_source(
        spatial,
        provider=provider,
        match_id=match_id,
        available_periods=available_periods,
    )
    metadata_by_id = _index_source(
        pressure_metadata,
        provider=provider,
        match_id=match_id,
        available_periods=available_periods,
    )
    actions_by_id = _index_source(
        actions,
        provider=provider,
        match_id=match_id,
        available_periods=available_periods,
    )

    if set(spatial_by_id) != set(metadata_by_id):
        raise ValueError("Spatial and pressure metadata coverage mismatch")

    expected_action_ids = {
        event_id
        for event_id, event in spatial_by_id.items()
        if event.event_type in CONTROL_EVENT_TYPES
    }
    if set(actions_by_id) != expected_action_ids:
        raise ValueError("Control action coverage mismatch")

    for event_id, spatial_event in spatial_by_id.items():
        metadata = metadata_by_id[event_id]
        for field in MatchEvent.model_fields:
            if getattr(spatial_event, field) != getattr(metadata, field):
                raise ValueError(f"Metadata header mismatch: {field}")

    for event_id, action in actions_by_id.items():
        spatial_event = spatial_by_id[event_id]
        metadata = metadata_by_id[event_id]
        for field in MatchEvent.model_fields:
            if getattr(action, field) != getattr(spatial_event, field):
                raise ValueError(f"Action header mismatch: {field}")
        for field in ("under_pressure", "counterpress"):
            if hasattr(action, field) and getattr(action, field) is not getattr(metadata, field):
                raise ValueError(f"Pressure attribute mismatch: {field}")

    rows: list[ControlRow] = []
    player_groups: dict[tuple[str, str], list[ControlRow]] = defaultdict(list)
    team_groups: dict[str, list[ControlRow]] = defaultdict(list)
    player_teams: dict[str, str] = {}
    ordered_actions = sorted(actions, key=lambda item: item.source_index)

    for action in ordered_actions:
        if action.period not in selected_periods:
            continue

        team_id = action.external_team_id
        player_id = action.external_player_id
        if team_id is None or player_id is None:
            raise ValueError("Control statistics require actor team and player")
        if player_id in player_teams and player_teams[player_id] != team_id:
            raise ValueError("Control player belongs to multiple teams")
        player_teams[player_id] = team_id

        row: ControlRow = (
            classify_ball_control_evidence(action, spatial_by_id[action.external_event_id]),
            metadata_by_id[action.external_event_id],
        )
        rows.append(row)
        player_groups[(team_id, player_id)].append(row)
        team_groups[team_id].append(row)

    return BallControlStatisticsResult(
        **_summarize(rows).model_dump(),
        external_match_id=match_id,
        events_sha256=events_sha256,
        available_periods=tuple(sorted(available_periods)),
        periods=selected_periods,
        players=tuple(
            PlayerBallControlStatistics(
                **_summarize(group).model_dump(),
                external_team_id=team_id,
                external_player_id=player_id,
            )
            for (team_id, player_id), group in sorted(player_groups.items())
        ),
        teams=tuple(
            TeamBallControlStatistics(
                **_summarize(group).model_dump(),
                external_team_id=team_id,
                external_player_ids=tuple(
                    sorted(
                        player_id
                        for player_id, player_team in player_teams.items()
                        if player_team == team_id
                    )
                ),
            )
            for team_id, group in sorted(team_groups.items())
        ),
        excluded_shootout_action_event_ids=tuple(
            action.external_event_id for action in ordered_actions if action.period == 5
        ),
    )
