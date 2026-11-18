"""Shared catalogue definition models, without calculation or presentation code."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Group = Literal["player", "team"]
Unit = Literal["count", "percent", "expected_goals", "milliseconds"]


class MetricDefinition(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    id: str
    name: str
    family: Literal[
        "passes",
        "shots",
        "xg",
        "participation",
        "defense",
        "duels",
        "aerial",
        "dribbles",
        "goalkeepers",
        "creation",
        "movement",
        "ball_control",
    ]
    unit: Unit
    definition_version: str = "1.0.0"
    formula: str
    groups: tuple[Group, ...] = ("player", "team")
    providers: tuple[str, ...] = ("statsbomb",)
    filters: tuple[str, ...] = ("periods", "team_ids", "player_ids")
    aggregation: Literal["sum", "ratio_of_sums"] = "sum"
    denominator: str | None = None
    missing_policy: str
    implementation: str
    tests: tuple[str, ...]
    validation_notes: str
    kind: Literal["metric", "diagnostic"] = "metric"
    selection: tuple[tuple[str, str], ...] = ()
    execution_available: bool = False
