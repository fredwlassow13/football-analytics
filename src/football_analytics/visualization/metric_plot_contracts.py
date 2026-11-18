"""Reproducible metric plot requests and prepared observations."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from football_analytics.analytics.catalogue import get_metric
from football_analytics.analytics.contracts import Contract, MetricValue
from football_analytics.analytics.definitions import Group, MetricDefinition

HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
Marker = Literal["o", "s", "^", "D", "v", "P", "X"]


FlagOffset = tuple[
    Annotated[float, Field(ge=-40, le=40, allow_inf_nan=False)],
    Annotated[float, Field(ge=-40, le=40, allow_inf_nan=False)],
]


class PlotStyle(Contract):
    identity_style: Literal["symbol", "flag"] = "symbol"
    flag_width: float = Field(default=14.0, ge=6, le=40, allow_inf_nan=False)
    flag_offset: FlagOffset = (0.0, 0.0)
    team_flag_offsets: dict[str, FlagOffset] = Field(default_factory=dict)
    correlation_negative: HexColor = "#2563eb"
    correlation_neutral: HexColor = "#f8fafc"
    correlation_positive: HexColor = "#dc2626"
    color: HexColor = "#2563eb"
    background: HexColor = "#ffffff"
    text_color: HexColor = "#172033"
    grid_color: HexColor = "#d1d5db"
    palette: tuple[HexColor, ...] = (
        "#2563eb",
        "#ea580c",
        "#059669",
        "#9333ea",
        "#be123c",
        "#0891b2",
    )
    color_by_team: bool = True
    team_colors: dict[str, HexColor] = Field(default_factory=dict)
    marker: Marker = "o"
    team_markers: dict[str, Marker] = Field(default_factory=dict)
    series_colors: dict[str, HexColor] = Field(default_factory=dict)
    series_markers: dict[str, Marker] = Field(default_factory=dict)
    alpha: float = Field(default=0.8, gt=0, le=1, allow_inf_nan=False)
    width: float = Field(default=10.0, ge=4, le=24, allow_inf_nan=False)
    height: float = Field(default=7.0, ge=3, le=24, allow_inf_nan=False)
    dpi: int = Field(default=150, ge=72, le=300)
    point_size: float = Field(default=55.0, ge=5, le=500, allow_inf_nan=False)
    font_size: float = Field(default=10.0, ge=6, le=24, allow_inf_nan=False)
    show_grid: bool = True
    show_labels: bool = False
    show_legend: bool = True

    @model_validator(mode="after")
    def check_palette(self) -> Self:
        if not self.palette:
            raise ValueError("Provide at least one palette color")
        return self


class PlotRequest(Contract):
    plot_version: Literal["1.0.0", "1.1.0", "1.2.0", "1.3.0"] = "1.3.0"
    kind: Literal["bar", "scatter", "histogram", "box", "correlation", "pair", "line"]
    group_by: Group
    aggregation: Literal["per_match", "selected_matches"] = "per_match"
    x_metric: str | None = None
    additional_metrics: tuple[str, ...] = ()
    metric_labels: dict[str, str] = Field(default_factory=dict)
    correlation_min_samples: int = Field(default=3, ge=3, le=1000)
    y_metric: str | None = None
    x_label: str | None = Field(default=None, min_length=1, max_length=100)
    y_label: str | None = Field(default=None, min_length=1, max_length=100)
    size_metric: str | None = None
    match_ids: tuple[int, ...] = ()
    team_ids: tuple[str, ...] = ()
    player_ids: tuple[str, ...] = ()
    top_n: int | None = Field(default=None, ge=1, le=100)
    bins: int = Field(default=15, ge=1, le=100)
    review_policy: Literal["include_with_warning", "exclude"] = "include_with_warning"
    title: str | None = Field(default=None, min_length=1, max_length=180)
    output_format: Literal["png", "svg"] = "png"
    style: PlotStyle = Field(default_factory=PlotStyle)

    @property
    def metric_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                m
                for m in (self.x_metric, self.y_metric, self.size_metric, *self.additional_metrics)
                if m is not None
            )
        )

    @model_validator(mode="after")
    def check_selection(self) -> Self:
        if self.kind in {"box", "correlation", "pair", "line"} and self.plot_version != "1.3.0":
            raise ValueError("Advanced plots require plot version 1.3.0")
        if self.kind == "line":
            if self.x_metric is not None or self.y_metric is None:
                raise ValueError("Line requires Y metric and uses observation order on X")
            if self.aggregation != "per_match":
                raise ValueError("Line requires per-match observations")
            series = self.player_ids if self.group_by == "player" else self.team_ids
            if not series or len(series) > 8:
                raise ValueError("Line requires one to eight explicit entities")
        elif self.x_metric is None:
            raise ValueError("Plot requires an X metric")
        if self.additional_metrics:
            if self.kind not in {"correlation", "pair"}:
                raise ValueError("Additional metrics apply only to correlation and pair plots")
            all_metrics = (self.x_metric, *self.additional_metrics)
            if len(all_metrics) != len(set(all_metrics)):
                raise ValueError("Duplicate multivariate metric")
        if self.kind in {"correlation", "pair"} and not 2 <= len(self.metric_ids) <= 6:
            raise ValueError("Select two to six multivariate metrics")
        if any(
            key not in self.metric_ids or not label.strip() or len(label) > 100
            for key, label in self.metric_labels.items()
        ):
            raise ValueError("Metric labels must refer to selected metrics and be nonempty")
        if self.style.identity_style == "flag":
            if self.plot_version not in {"1.2.0", "1.3.0"}:
                raise ValueError("Centered flag identity requires plot version 1.2.0")
            if self.size_metric is not None:
                raise ValueError("Fixed-size flag identity cannot encode a size metric")
            if self.style.flag_offset != (0.0, 0.0) or any(
                offset != (0.0, 0.0) for offset in self.style.team_flag_offsets.values()
            ):
                raise ValueError("Centered flags cannot have position offsets")
            if self.kind != "scatter":
                raise ValueError("Flag identity currently requires scatter")
        if (self.y_metric is not None) != (self.kind in {"scatter", "line"}):
            raise ValueError("Scatter and line require a Y metric")
        if self.y_label is not None and self.kind not in {"scatter", "line"}:
            raise ValueError("Y label applies only to scatter and line")
        if self.size_metric is not None and self.kind != "scatter":
            raise ValueError("Point size metric requires scatter")
        if self.top_n is not None and self.kind != "bar":
            raise ValueError("Top N applies only to bars")
        for values in (self.match_ids, self.team_ids, self.player_ids):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate plot selection")
        if any(mid <= 0 for mid in self.match_ids):
            raise ValueError("Match IDs must be positive")
        if any(
            not value.strip() or value != value.strip()
            for value in (*self.team_ids, *self.player_ids)
        ):
            raise ValueError("Entity IDs must be nonempty and trimmed")
        if self.group_by == "team" and self.player_ids:
            raise ValueError("Team plots cannot filter players")
        for identifier in self.metric_ids:
            metric = get_metric(identifier)
            if metric.kind != "metric" or not metric.execution_available:
                raise ValueError("Select an enabled performance metric, not a technical diagnostic")
            if self.group_by not in metric.groups:
                raise ValueError("Metric does not support the selected population")
        return self


class PlotSource(Contract):
    match_id: int = Field(gt=0)
    export_id: UUID
    file: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlotObservation(Contract):
    match_ids: tuple[int, ...]
    team_id: str
    team_name: str
    player_id: str | None
    label: str
    values: tuple[MetricValue, ...]


class ExcludedObservation(Contract):
    observation: PlotObservation
    reasons: tuple[str, ...]


class PreparedPlot(Contract):
    request: PlotRequest
    batch_id: UUID
    index_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sources: tuple[PlotSource, ...]
    definitions: tuple[MetricDefinition, ...]
    population_count: int = Field(ge=0)
    points: tuple[PlotObservation, ...]
    excluded: tuple[ExcludedObservation, ...]
    omitted_by_top_n: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check_population(self) -> Self:
        if tuple(d.id for d in self.definitions) != self.request.metric_ids:
            raise ValueError("Plot definitions differ from request")
        source_ids = [s.match_id for s in self.sources]
        if not source_ids or len(source_ids) != len(set(source_ids)):
            raise ValueError("Plot requires distinct match sources")
        if self.request.match_ids and set(self.request.match_ids) != set(source_ids):
            raise ValueError("Plot sources differ from requested matches")
        for row in (*self.points, *(e.observation for e in self.excluded)):
            if not row.match_ids or not set(row.match_ids).issubset(source_ids):
                raise ValueError("Observation references an unselected match")
            if (row.player_id is not None) != (self.request.group_by == "player"):
                raise ValueError("Observation population differs from plot request")
        if self.population_count != len(self.points) + len(self.excluded) + self.omitted_by_top_n:
            raise ValueError("Plot population accounting differs")
        for point in self.points:
            if tuple(v.metric_id for v in point.values) != self.request.metric_ids:
                raise ValueError("Point metrics differ from request")
            if any(v.status != "available" or v.value is None for v in point.values):
                raise ValueError("Only available numeric values can be plotted")
            if self.request.review_policy == "exclude" and any(
                v.review_reasons for v in point.values
            ):
                raise ValueError("Reviewed observation violates exclusion policy")
        return self
