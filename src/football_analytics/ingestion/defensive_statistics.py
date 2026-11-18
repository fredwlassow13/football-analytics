from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from football_analytics.ingestion.defensive_scope import (
    DefensiveEvent,
    select_defensive_periods,
)

FLAG_FIELDS: dict[str, tuple[str, ...]] = {
    "Ball Recovery": ("recovery_failure", "offensive"),
    "Block": ("deflection", "offensive", "save_block", "counterpress"),
    "Clearance": (
        "aerial_won",
        "head",
        "left_foot",
        "right_foot",
        "other",
    ),
    "Dribbled Past": ("counterpress",),
    "Duel": (),
    "Foul Committed": (
        "offensive",
        "advantage",
        "penalty",
        "counterpress",
    ),
    "Foul Won": ("defensive", "advantage", "penalty"),
    "Interception": (),
    "Pressure": ("counterpress",),
}

CATEGORY_FIELDS: dict[str, tuple[str, ...]] = {
    "Clearance": ("body_part",),
    "Duel": ("duel_type", "outcome"),
    "Foul Committed": ("foul_type", "card"),
    "Interception": ("outcome",),
}

COVERAGE_FIELDS: dict[str, tuple[str, ...]] = {
    "Pressure": ("duration_seconds", "related_event_ids"),
    "Dribbled Past": ("related_event_ids",),
    "Foul Committed": ("related_event_ids",),
    "Foul Won": ("related_event_ids",),
}


class FlagCounts(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    true: int = Field(ge=0)
    false: int = Field(ge=0)
    unknown: int = Field(ge=0)


class CategoryCounts(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    known: dict[str, int]
    missing: int = Field(ge=0)


class FieldCoverage(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    present: int = Field(ge=0)
    missing: int = Field(ge=0)


class DefensiveActionStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    event_type: str
    event_count: int = Field(gt=0)
    flags: dict[str, FlagCounts]
    categories: dict[str, CategoryCounts]
    coverage: dict[str, FieldCoverage]
    external_event_ids: tuple[str, ...]


class PlayerDefensiveStatistics(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str
    external_match_id: str
    external_team_id: str
    external_player_id: str
    policy_version: Literal["1.0.0"] = "1.0.0"
    population_basis: Literal["observed_source_events"] = "observed_source_events"
    periods: tuple[int, ...]
    event_count: int = Field(gt=0)
    actions: tuple[DefensiveActionStatistics, ...]
    external_event_ids: tuple[str, ...]


def summarize_defensive_actions(
    events: Sequence[DefensiveEvent],
) -> tuple[DefensiveActionStatistics, ...]:
    """Summarize an already validated, selected group of source events."""
    grouped: dict[str, list[DefensiveEvent]] = defaultdict(list)

    for event in sorted(events, key=lambda item: item.source_index):
        grouped[event.event_type].append(event)

    result: list[DefensiveActionStatistics] = []

    for event_type, group in sorted(grouped.items()):
        rows: list[dict[str, object]] = [event.model_dump() for event in group]

        flags: dict[str, FlagCounts] = {}
        for field in FLAG_FIELDS[event_type]:
            flags[field] = FlagCounts(
                true=sum(row[field] is True for row in rows),
                false=sum(row[field] is False for row in rows),
                unknown=sum(row[field] is None for row in rows),
            )

        categories: dict[str, CategoryCounts] = {}
        for field in CATEGORY_FIELDS.get(event_type, ()):
            known = Counter(str(row[field]) for row in rows if row[field] is not None)
            categories[field] = CategoryCounts(
                known=dict(sorted(known.items())),
                missing=sum(row[field] is None for row in rows),
            )

        coverage: dict[str, FieldCoverage] = {}
        for field in COVERAGE_FIELDS.get(event_type, ()):
            coverage[field] = FieldCoverage(
                present=sum(row[field] is not None for row in rows),
                missing=sum(row[field] is None for row in rows),
            )

        result.append(
            DefensiveActionStatistics(
                event_type=event_type,
                event_count=len(group),
                flags=flags,
                categories=categories,
                coverage=coverage,
                external_event_ids=tuple(event.external_event_id for event in group),
            )
        )

    return tuple(result)


def aggregate_player_defensive_actions(
    events: Sequence[DefensiveEvent],
    *,
    available_periods: tuple[int, ...],
    periods: tuple[int, ...] | None = None,
) -> list[PlayerDefensiveStatistics]:
    selected = select_defensive_periods(
        events,
        available_periods=available_periods,
        periods=periods,
    )

    player_teams: dict[str, str] = {}
    grouped: dict[tuple[str, str], list[DefensiveEvent]] = defaultdict(list)

    for event in selected:
        team_id = event.external_team_id
        player_id = event.external_player_id

        if team_id is None or player_id is None:
            raise ValueError("Defensive statistics require team and player references")

        if player_id in player_teams and player_teams[player_id] != team_id:
            raise ValueError("Player belongs to multiple teams")

        player_teams[player_id] = team_id
        grouped[(team_id, player_id)].append(event)

    selected_periods = tuple(sorted(available_periods if periods is None else periods))

    return [
        PlayerDefensiveStatistics(
            provider=group[0].provider,
            external_match_id=group[0].external_match_id,
            external_team_id=team_id,
            external_player_id=player_id,
            periods=selected_periods,
            event_count=len(group),
            actions=summarize_defensive_actions(group),
            external_event_ids=tuple(event.external_event_id for event in group),
        )
        for (team_id, player_id), group in sorted(grouped.items())
    ]
