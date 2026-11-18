"""Exploratory plots with explicit populations, missingness and ordering."""

from collections import defaultdict
from math import isfinite
from statistics import correlation
from textwrap import fill
from typing import TypedDict

import numpy as np
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator

from football_analytics.visualization.metric_plot_contracts import PlotObservation, PreparedPlot

_UNITS = {"count": "contagem", "percent": "%", "expected_goals": "xG", "milliseconds": "ms"}


class CorrelationResult(TypedDict):
    method: str
    missing_policy: str
    metric_ids: list[str]
    n: int
    matrix: list[list[float | None]]
    warnings: list[str]


class LineSeries(TypedDict):
    label: str
    team_id: str
    player_id: str | None
    match_ids: list[int]
    values: list[float | None]


def numeric_value(point: PlotObservation, metric: str) -> float:
    value = next(v.value for v in point.values if v.metric_id == metric)
    if value is None or not isfinite(number := float(value)):
        raise ValueError("Plot requires a finite numeric coordinate")
    return number


def correlation_summary(data: PreparedPlot) -> CorrelationResult:
    ids = list(data.request.metric_ids)
    columns = {mid: [numeric_value(p, mid) for p in data.points] for mid in ids}
    n = len(data.points)
    warnings = []
    if n < data.request.correlation_min_samples:
        warnings.append("insufficient_observations")
    constant = {mid for mid in ids if len(set(columns[mid])) < 2}
    warnings.extend(f"{mid}:constant" for mid in sorted(constant))
    matrix: list[list[float | None]] = []
    for left in ids:
        row: list[float | None] = []
        for right in ids:
            if n < data.request.correlation_min_samples or left in constant or right in constant:
                row.append(None)
            else:
                row.append(max(-1.0, min(1.0, correlation(columns[left], columns[right]))))
        matrix.append(row)
    return {
        "method": "pearson",
        "missing_policy": "complete_case_across_selected_metrics",
        "metric_ids": ids,
        "n": n,
        "matrix": matrix,
        "warnings": warnings,
    }


def line_series(data: PreparedPlot) -> list[LineSeries]:
    order = {source.match_id: n for n, source in enumerate(data.sources)}
    valid = {(p.match_ids, p.team_id, p.player_id) for p in data.points}
    groups: dict[tuple[str, str | None], list[PlotObservation]] = defaultdict(list)
    for row in (*data.points, *(e.observation for e in data.excluded)):
        if len(row.match_ids) != 1:
            raise ValueError("Line requires single-match observations")
        groups[row.team_id, row.player_id].append(row)
    metric = data.request.y_metric
    if metric is None:
        raise ValueError("Line requires a Y metric")
    result = []
    for (team, player), rows in sorted(groups.items(), key=lambda p: repr(p[0])):
        rows.sort(key=lambda p: order[p.match_ids[0]])
        matches = [p.match_ids[0] for p in rows]
        if len(matches) != len(set(matches)):
            raise ValueError("Duplicate line entity/match")
        series: LineSeries = {
            "label": rows[0].label,
            "team_id": team,
            "player_id": player,
            "match_ids": matches,
            "values": [
                numeric_value(p, metric) if (p.match_ids, p.team_id, p.player_id) in valid else None
                for p in rows
            ],
        }
        result.append(series)
    return result


def advanced_analysis(data: PreparedPlot) -> dict[str, object]:
    if data.request.kind == "correlation":
        return dict(correlation_summary(data))
    if data.request.kind == "line":
        return {
            "order": "entity_observations_in_export_index_order",
            "time_scale": False,
            "gap_policy": "excluded_values_are_null",
            "series": line_series(data),
        }
    if data.request.kind == "pair":
        return {
            "metric_ids": list(data.request.metric_ids),
            "n": len(data.points),
            "missing_policy": "complete_case_across_selected_metrics",
        }
    if data.request.kind == "box":
        groups: dict[str, list[float]] = defaultdict(list)
        metric = data.request.x_metric
        if metric is None:
            raise ValueError("Box plot requires a metric")
        for point in data.points:
            groups[point.team_id].append(numeric_value(point, metric))
        summaries = []
        for team, values in sorted(groups.items()):
            q1, median, q3 = [float(v) for v in np.quantile(values, [0.25, 0.5, 0.75])]
            low, high = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
            within = [v for v in values if low <= v <= high]
            summaries.append(
                {
                    "team_id": team,
                    "n": len(values),
                    "q1": q1,
                    "median": median,
                    "q3": q3,
                    "whisker_low": min(within),
                    "whisker_high": max(within),
                    "outliers": [v for v in values if v < low or v > high],
                }
            )
        return {"whisker_factor": 1.5, "quartile_method": "linear", "groups": summaries}
    raise ValueError("Unsupported advanced plot")


def render_advanced_plot(data: PreparedPlot) -> Figure:
    request, style = data.request, data.request.style
    figure = Figure(
        figsize=(style.width, style.height),
        dpi=style.dpi,
        facecolor=style.background,
        layout="constrained",
    )
    FigureCanvasAgg(figure)
    definitions = {d.id: d for d in data.definitions}
    teams = sorted(
        {p.team_id for p in data.points} | {e.observation.team_id for e in data.excluded}
    )
    colors = {
        team: style.team_colors.get(team, style.palette[n % len(style.palette)])
        if style.color_by_team
        else style.color
        for n, team in enumerate(teams)
    }
    names = {
        p.team_id: p.team_name for p in (*data.points, *(e.observation for e in data.excluded))
    }

    def label(mid: str) -> str:
        definition = definitions[mid]
        alias = request.metric_labels.get(mid)
        if alias is None:
            alias = request.x_label if mid == request.x_metric else request.y_label
        return f"{alias or definition.name} ({_UNITS[definition.unit]})"

    def decorate(axis: Axes, *, grid: bool = True) -> None:
        axis.set_facecolor(style.background)
        axis.tick_params(labelsize=style.font_size, labelcolor=style.text_color)
        axis.xaxis.label.set_color(style.text_color)
        axis.yaxis.label.set_color(style.text_color)
        axis.xaxis.label.set_fontsize(style.font_size)
        axis.yaxis.label.set_fontsize(style.font_size)
        for spine in axis.spines.values():
            spine.set_color(style.grid_color)
        if style.show_grid and grid:
            axis.set_axisbelow(True)
            axis.grid(True, alpha=0.45, color=style.grid_color)

    if request.kind == "pair":
        ids = request.metric_ids
        axes = figure.subplots(len(ids), len(ids), squeeze=False)
        columns = {mid: [numeric_value(p, mid) for p in data.points] for mid in ids}
        for row, y_metric in enumerate(ids):
            for column, x_metric in enumerate(ids):
                axis = axes[row, column]
                if row == column:
                    axis.hist(
                        columns[x_metric], bins=request.bins, color=style.color, alpha=style.alpha
                    )
                else:
                    for team in teams:
                        indexes = [i for i, p in enumerate(data.points) if p.team_id == team]
                        axis.scatter(
                            [columns[x_metric][i] for i in indexes],
                            [columns[y_metric][i] for i in indexes],
                            color=colors[team],
                            marker=style.team_markers.get(team, style.marker),
                            s=style.point_size,
                            alpha=style.alpha,
                        )
                if row == len(ids) - 1:
                    axis.set_xlabel(fill(label(x_metric), 26))
                if column == 0:
                    axis.set_ylabel("Observações" if row == column else fill(label(y_metric), 26))
                decorate(axis)
        if style.show_legend and style.color_by_team:
            handles = [
                Line2D(
                    [],
                    [],
                    color=colors[t],
                    marker=style.team_markers.get(t, style.marker),
                    linestyle="none",
                    label=names[t],
                )
                for t in teams
            ]
            legend = axes[0, 0].legend(
                handles=handles,
                fontsize=max(6.0, style.font_size - 1),
                facecolor=style.background,
                edgecolor=style.grid_color,
            )
            for text in legend.get_texts():
                text.set_color(style.text_color)
        title = request.title or "Relações entre métricas"
    else:
        axis = figure.subplots()
        if request.kind == "correlation":
            summary = correlation_summary(data)
            matrix = np.array(
                [[float("nan") if v is None else v for v in row] for row in summary["matrix"]]
            )
            cmap = LinearSegmentedColormap.from_list(
                "metric_correlation",
                [style.correlation_negative, style.correlation_neutral, style.correlation_positive],
            ).with_extremes(bad=style.grid_color)
            image = axis.imshow(np.ma.masked_invalid(matrix), vmin=-1, vmax=1, cmap=cmap)
            labels = [fill(label(mid), 24) for mid in request.metric_ids]
            axis.set_xticks(range(len(labels)), labels=labels, rotation=30, ha="right")
            axis.set_yticks(range(len(labels)), labels=labels)
            for row in range(len(labels)):
                for column in range(len(labels)):
                    value = summary["matrix"][row][column]
                    axis.text(
                        column,
                        row,
                        "N/A" if value is None else f"{value:.2f}",
                        ha="center",
                        va="center",
                        fontsize=style.font_size,
                        color="white"
                        if value is not None and abs(value) > 0.65
                        else style.text_color,
                    )
            colorbar = figure.colorbar(image, ax=axis, label="Pearson r")
            colorbar.ax.tick_params(labelcolor=style.text_color)
            colorbar.ax.yaxis.label.set_color(style.text_color)
            decorate(axis, grid=False)
            title = request.title or "Correlações entre métricas"
        elif request.kind == "box":
            metric = request.x_metric
            if metric is None:
                raise ValueError("Box plot requires a metric")
            groups = {
                team: [numeric_value(p, metric) for p in data.points if p.team_id == team]
                for team in teams
            }
            groups = {team: values for team, values in groups.items() if values}
            result = axis.boxplot(
                list(groups.values()),
                tick_labels=[f"{names[t]}\n(n={len(groups[t])})" for t in groups],
                patch_artist=True,
                whis=1.5,
                showfliers=True,
            )
            for patch, team in zip(result["boxes"], groups, strict=True):
                patch.set_facecolor(colors[team])
                patch.set_alpha(style.alpha)
            for part in ("whiskers", "caps", "medians", "fliers"):
                for artist in result[part]:
                    artist.set_color(style.text_color)
            axis.set_ylabel(label(metric))
            decorate(axis)
            title = request.title or "Distribuição por equipe"
        elif request.kind == "line":
            metric = request.y_metric
            if metric is None:
                raise ValueError("Line requires a Y metric")
            series = line_series(data)
            for number, item in enumerate(series):
                key = item["player_id"] or item["team_id"]
                default_color = colors[item["team_id"]]
                if request.group_by == "player" and style.color_by_team:
                    default_color = style.palette[number % len(style.palette)]
                color = style.series_colors.get(key, default_color)
                marker = style.series_markers.get(
                    key, style.team_markers.get(item["team_id"], style.marker)
                )
                axis.plot(
                    range(1, len(item["values"]) + 1),
                    [float("nan") if v is None else v for v in item["values"]],
                    color=color,
                    marker=marker,
                    alpha=style.alpha,
                    label=item["label"],
                )
            axis.set_xlabel(
                request.x_label or "Sequência das observações de cada entidade no índice"
            )
            axis.set_ylabel(label(metric))
            axis.xaxis.set_major_locator(MaxNLocator(integer=True))
            axis.set_xlim(left=0.75)
            axis.set_ylim(bottom=0)
            decorate(axis)
            if style.show_legend:
                legend = axis.legend(
                    fontsize=style.font_size, facecolor=style.background, edgecolor=style.grid_color
                )
                for text in legend.get_texts():
                    text.set_color(style.text_color)
            title = request.title or "Métrica por sequência de partidas"
        else:
            raise ValueError("Unsupported advanced plot")
        axis.set_title(title, fontsize=style.font_size + 3, color=style.text_color, pad=12)
    if request.kind == "pair":
        figure.suptitle(title, fontsize=style.font_size + 3, color=style.text_color)
    reviewed = sum(any(v.review_reasons for v in p.values) for p in data.points)
    population = "jogador × partida" if request.group_by == "player" else "equipe × partida"
    if request.aggregation == "selected_matches":
        population = "agregado por entidade nas partidas selecionadas"
    footer = (
        f"{population} | {len(data.points)} válidas | {len(data.excluded)} excluídas | "
        f"{reviewed} com ressalvas"
    )
    if request.kind in {"correlation", "pair"}:
        footer += "\nMesma amostra completa em todas as métricas; análise descritiva"
    if request.kind == "correlation" and correlation_summary(data)["warnings"]:
        footer += " | N/A: constante ou amostra insuficiente"
    if request.kind == "line":
        footer += "\nOrdem do índice, sem escala de tempo; lacunas preservadas"
    figure.supxlabel(footer, fontsize=max(6.0, style.font_size - 2), color=style.text_color)
    return figure
