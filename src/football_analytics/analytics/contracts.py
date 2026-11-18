"""Validated query/result contracts. These models do not execute calculations."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from football_analytics.analytics.catalogue import Group, Unit, get_metric


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class MetricQuery(Contract):
    contract_version: Literal["1.0.0"] = "1.0.0"
    provider: Literal["statsbomb"] = "statsbomb"
    match_ids: tuple[int, ...]
    group_by: Group
    metrics: tuple[str, ...]
    aggregation: Literal["per_match", "selected_matches"] = "per_match"
    periods: tuple[int, ...] | None = None
    team_ids: tuple[str, ...] = ()
    player_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def check_selection(self) -> Self:
        if not self.match_ids or any(mid <= 0 for mid in self.match_ids):
            raise ValueError("Provide positive match IDs")
        if not self.metrics:
            raise ValueError("Select at least one metric")
        for label, values in (
            ("match_ids", self.match_ids),
            ("metrics", self.metrics),
            ("team_ids", self.team_ids),
            ("player_ids", self.player_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate {label}")
        if any(not s.strip() or s != s.strip() for s in (*self.team_ids, *self.player_ids)):
            raise ValueError("Entity IDs must be nonempty, without surrounding whitespace")
        if self.periods is not None and (
            not self.periods
            or len(self.periods) != len(set(self.periods))
            or not set(self.periods).issubset({1, 2, 3, 4})
        ):
            raise ValueError("Select distinct playing periods 1–4; shootout is excluded")
        if self.group_by == "team" and self.player_ids:
            raise ValueError("Player filters are not supported for complete team metrics")
        for mid in self.metrics:
            metric = get_metric(mid)
            if self.group_by not in metric.groups:
                raise ValueError(f"Metric {mid} does not support group {self.group_by}")
            if self.provider not in metric.providers:
                raise ValueError(f"Metric {mid} does not support provider {self.provider}")
        return self


class Coverage(Contract):
    source_complete: bool
    population: Literal["observed", "presence_confirmed", "unknown", "not_applicable"]
    eligible_events: int = Field(ge=0)
    covered_events: int = Field(ge=0)

    @model_validator(mode="after")
    def check_counts(self) -> Self:
        if self.covered_events > self.eligible_events:
            raise ValueError("Covered events exceed eligible events")
        return self

    @property
    def complete(self) -> bool:
        return self.source_complete and self.covered_events == self.eligible_events


class MetricValue(Contract):
    metric_id: str
    definition_version: str = "1.0.0"
    unit: Unit
    status: Literal["available", "unknown", "not_applicable", "incomplete"]
    value: int | Decimal | None
    coverage: Coverage
    numerator: int | Decimal | None = None
    denominator: int | Decimal | None = None
    reasons: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def check_value(self) -> Self:
        metric = get_metric(self.metric_id)
        if self.unit != metric.unit or self.definition_version != metric.definition_version:
            raise ValueError("Unit or definition version differs from catalogue")
        for number in (self.value, self.numerator, self.denominator):
            if number is not None and (
                (isinstance(number, Decimal) and not number.is_finite()) or number < 0
            ):
                raise ValueError("Metric values must be finite and nonnegative")
        if self.status in {"unknown", "not_applicable"} and self.value is not None:
            raise ValueError("Unknown/not-applicable values must be null")
        if self.status != "available" and not self.reasons:
            raise ValueError("Non-available results need an explicit reason")
        if self.status == "available":
            if self.value is None or not self.coverage.complete:
                raise ValueError("Available values require complete coverage and a value")
        if self.status in {"available", "incomplete"} and self.coverage.population in {
            "unknown",
            "not_applicable",
        }:
            raise ValueError("Numeric results require an applicable, evidenced population")
        if self.status == "not_applicable" and self.coverage.population != "not_applicable":
            raise ValueError("Not-applicable result requires not-applicable population")
        if self.status == "incomplete" and self.coverage.complete:
            raise ValueError("Incomplete status requires incomplete coverage")
        if (
            self.metric_id.endswith(".xg")
            and self.status == "incomplete"
            and self.value is not None
        ):
            raise ValueError(
                "Complete xG cannot expose a partial sum; use "
                + self.metric_id.removesuffix(".xg")
                + ".xg_available"
            )
        if self.value is not None and self.unit in {"count", "milliseconds"}:
            if type(self.value) is not int:
                raise ValueError("Counts and milliseconds require integer values")
        if metric.aggregation == "ratio_of_sums" and self.value is not None:
            if self.numerator is None or self.denominator is None or self.denominator <= 0:
                raise ValueError("Percentage requires numerator and positive denominator")
            if self.numerator > self.denominator or self.value > 100:
                raise ValueError("Percentage exceeds its valid range")
            expected = (Decimal(self.numerator) * 100 / Decimal(self.denominator)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if self.value != expected:
                raise ValueError("Percentage differs from its numerator and denominator")
        return self


class SourceReference(Contract):
    match_id: int = Field(gt=0)
    events_payload_id: UUID
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    available_periods: tuple[int, ...]
    lineups_payload_id: UUID | None = None
    lineups_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    participation_calculation_id: UUID | None = None

    @model_validator(mode="after")
    def check_lineup_pair(self) -> Self:
        if len(self.available_periods) != len(set(self.available_periods)) or set(
            self.available_periods
        ) not in ({1, 2}, {1, 2, 3, 4}):
            raise ValueError("Available periods must describe a complete match")
        if (self.lineups_payload_id is None) != (self.lineups_sha256 is None):
            raise ValueError("Lineup payload ID and hash must be supplied together")
        if self.participation_calculation_id is not None and self.lineups_payload_id is None:
            raise ValueError("Participation calculation requires lineup provenance")
        return self


class ResultRow(Contract):
    match_ids: tuple[int, ...]
    team_id: str = Field(min_length=1)
    player_id: str | None = Field(default=None, min_length=1)
    values: tuple[MetricValue, ...]


class QueryResult(Contract):
    contract_version: Literal["1.0.0"] = "1.0.0"
    query: MetricQuery
    sources: tuple[SourceReference, ...]
    rows: tuple[ResultRow, ...]

    @model_validator(mode="after")
    def check_alignment(self) -> Self:
        source_ids = [s.match_id for s in self.sources]
        if len(source_ids) != len(set(source_ids)) or set(source_ids) != set(self.query.match_ids):
            raise ValueError("Sources must cover exactly the requested matches without duplicates")
        if self.query.periods is not None and any(
            not set(self.query.periods).issubset(s.available_periods) for s in self.sources
        ):
            raise ValueError("Requested periods are unavailable in a selected match")
        if "participation.elapsed_on_field_ms" in self.query.metrics and any(
            s.participation_calculation_id is None for s in self.sources
        ):
            raise ValueError("Participation metric requires calculation provenance for every match")
        seen = set()
        for row in self.rows:
            if not row.match_ids or len(row.match_ids) != len(set(row.match_ids)):
                raise ValueError("Row needs distinct match IDs")
            if not set(row.match_ids).issubset(self.query.match_ids):
                raise ValueError("Row references an unrequested match")
            if self.query.aggregation == "per_match" and len(row.match_ids) != 1:
                raise ValueError("Per-match rows must reference one match")
            if self.query.aggregation == "selected_matches" and set(row.match_ids) != set(
                self.query.match_ids
            ):
                raise ValueError("Aggregated rows must describe the full requested selection")
            if (row.player_id is not None) != (self.query.group_by == "player"):
                raise ValueError("Row entity differs from requested grouping")
            if self.query.team_ids and row.team_id not in self.query.team_ids:
                raise ValueError("Row team is outside the requested selection")
            if self.query.player_ids and row.player_id not in self.query.player_ids:
                raise ValueError("Row player is outside the requested selection")
            metrics = [v.metric_id for v in row.values]
            if len(metrics) != len(set(metrics)) or set(metrics) != set(self.query.metrics):
                raise ValueError("Each row must contain exactly the requested metrics")
            key = (tuple(sorted(row.match_ids)), row.team_id, row.player_id)
            if key in seen:
                raise ValueError("Duplicate result row")
            seen.add(key)
        return self
