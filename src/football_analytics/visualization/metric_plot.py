"""Render configured bars, scatter plots and histograms from exported metrics."""

import hashlib
import io
import json
from collections import Counter
from math import isfinite
from pathlib import Path

from matplotlib.artist import Artist
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.image import imread
from matplotlib.lines import Line2D
from matplotlib.offsetbox import (
    AnchoredOffsetbox,
    AnnotationBbox,
    DrawingArea,
    HPacker,
    OffsetImage,
    TextArea,
    VPacker,
)

from football_analytics.visualization.advanced_metric_plots import (
    advanced_analysis,
    render_advanced_plot,
)
from football_analytics.visualization.metric_plot_contracts import (
    PlotObservation,
    PlotRequest,
    PreparedPlot,
)
from football_analytics.visualization.metric_plot_data import prepare_metric_plot
from football_analytics.visualization.team_flags import ResolvedFlags, resolve_team_flags

_UNITS = {"count": "contagem", "percent": "%", "expected_goals": "xG", "milliseconds": "ms"}


def _value(point: PlotObservation, identifier: str) -> float:
    value = next(v.value for v in point.values if v.metric_id == identifier)
    if value is None or not isfinite(number := float(value)):
        raise ValueError("Metric value is not representable as a finite plot coordinate")
    return number


def render_metric_plot(
    data: PreparedPlot,
    *,
    flags: ResolvedFlags | None = None,
) -> Figure:
    data = PreparedPlot.model_validate_json(data.model_dump_json())
    if not data.points:
        counts = Counter(reason for e in data.excluded for reason in e.reasons)
        raise ValueError(f"No plottable observations; exclusions: {dict(counts)}")
    request, style = data.request, data.request.style
    if request.kind in {"box", "correlation", "pair", "line"}:
        return render_advanced_plot(data)
    x_metric = request.x_metric
    if x_metric is None:
        raise ValueError("Plot requires an X metric")
    if style.identity_style == "flag" and flags is None:
        raise ValueError("Flag identity requires resolved local assets")
    if style.identity_style == "flag" and flags is not None and flags.provider != "statsbomb":
        raise ValueError("Flag provider differs from plot provider")
    figure = Figure(
        figsize=(style.width, style.height),
        dpi=style.dpi,
        facecolor=style.background,
        layout="constrained",
    )
    FigureCanvasAgg(figure)
    axes = figure.subplots()
    axes.set_facecolor(style.background)
    axes.tick_params(axis="both", labelsize=style.font_size, labelcolor=style.text_color)
    for spine in axes.spines.values():
        spine.set_color(style.grid_color)
    teams = sorted({p.team_id for p in data.points})
    colors = {
        team: style.team_colors.get(team, style.palette[number % len(style.palette)])
        if style.color_by_team
        else style.color
        for number, team in enumerate(teams)
    }
    definitions = {d.id: d for d in data.definitions}

    def axis_label(identifier: str, label: str | None = None) -> str:
        definition = definitions[identifier]
        name = label or request.metric_labels.get(identifier, definition.name)
        return f"{name} ({_UNITS[definition.unit]})"

    x_values = [_value(p, x_metric) for p in data.points]
    title = request.title or definitions[x_metric].name
    if request.kind == "bar":
        labels = [
            p.label
            + (
                f" · {p.match_ids[0]}"
                if request.aggregation == "per_match" and len(data.sources) > 1
                else ""
            )
            for p in data.points
        ]
        axes.barh(
            range(len(data.points)),
            x_values,
            color=[colors[p.team_id] for p in data.points],
            alpha=style.alpha,
        )
        axes.set_yticks(range(len(labels)), labels=labels)
        axes.invert_yaxis()
        axes.set_xlabel(
            axis_label(x_metric, request.x_label),
            color=style.text_color,
            fontsize=style.font_size,
        )
        axes.set_xlim(left=0)
    elif request.kind == "histogram":
        axes.hist(
            x_values,
            bins=request.bins,
            color=style.color,
            alpha=style.alpha,
            edgecolor=style.background,
        )
        axes.set_xlabel(
            axis_label(x_metric, request.x_label),
            color=style.text_color,
            fontsize=style.font_size,
        )
        axes.set_ylabel("Número de observações", color=style.text_color, fontsize=style.font_size)
    else:
        if request.y_metric is None:
            raise ValueError("Scatter requires a Y metric")
        y_values = [_value(p, request.y_metric) for p in data.points]
        sizes = [style.point_size] * len(data.points)
        if request.size_metric is not None:
            raw_sizes = [_value(p, request.size_metric) for p in data.points]
            low, high = min(raw_sizes), max(raw_sizes)
            if high > low:
                sizes = [style.point_size * (0.5 + 2 * (v - low) / (high - low)) for v in raw_sizes]
        axes.update_datalim(list(zip(x_values, y_values, strict=True)))
        axes.autoscale_view()
        for team in teams:
            if style.identity_style == "flag" and flags is not None and team in flags.assets:
                continue
            indexes = [i for i, p in enumerate(data.points) if p.team_id == team]
            axes.scatter(
                [x_values[i] for i in indexes],
                [y_values[i] for i in indexes],
                s=[sizes[i] for i in indexes],
                color=colors[team],
                alpha=style.alpha,
                marker=style.team_markers.get(team, style.marker),
            )
        if style.identity_style == "flag" and flags is not None:
            for x, y, point in zip(x_values, y_values, data.points, strict=True):
                asset = flags.assets.get(point.team_id)
                if asset is None:
                    continue
                image = imread(io.BytesIO(asset.image), format="png")
                box = OffsetImage(image, zoom=style.flag_width / image.shape[1])
                annotation = AnnotationBbox(
                    box,
                    (x, y),
                    xybox=(0.0, 0.0),
                    xycoords="data",
                    boxcoords="offset points",
                    box_alignment=(0.5, 0.5),
                    frameon=True,
                    pad=0,
                    bboxprops={
                        "edgecolor": style.grid_color,
                        "facecolor": style.background,
                        "linewidth": 0.5,
                    },
                )
                annotation.set_zorder(4)
                annotation.set_in_layout(False)
                axes.add_artist(annotation)
        if style.show_labels:
            for x, y, point in zip(x_values, y_values, data.points, strict=True):
                label = point.label + (
                    f" · {point.match_ids[0]}"
                    if request.aggregation == "per_match" and len(data.sources) > 1
                    else ""
                )
                label_offset = (4.0, 4.0)
                if (
                    style.identity_style == "flag"
                    and flags is not None
                    and point.team_id in flags.assets
                ):
                    label_offset = (style.flag_width / 2 + 3, style.flag_width * 0.375 + 3)
                axes.annotate(
                    label,
                    (x, y),
                    xytext=label_offset,
                    textcoords="offset points",
                    fontsize=style.font_size,
                    color=style.text_color,
                )
        axes.set_xlabel(
            axis_label(x_metric, request.x_label),
            color=style.text_color,
            fontsize=style.font_size,
        )
        axes.set_ylabel(
            axis_label(request.y_metric, request.y_label),
            color=style.text_color,
            fontsize=style.font_size,
        )
        axes.set_xlim(left=0)
        axes.set_ylim(bottom=0)
        if request.title is None:
            title = f"{definitions[x_metric].name} × {definitions[request.y_metric].name}"
    if style.show_legend and style.identity_style == "flag" and flags is not None:
        names = {p.team_id: p.team_name for p in data.points}
        legend_rows: list[Artist] = []
        for team in teams:
            asset = flags.assets.get(team)
            icon: OffsetImage | DrawingArea
            if asset is not None:
                image = imread(io.BytesIO(asset.image), format="png")
                icon = OffsetImage(image, zoom=style.flag_width / image.shape[1])
            else:
                icon = DrawingArea(style.flag_width, style.flag_width)
                icon.add_artist(
                    Line2D(
                        [style.flag_width / 2],
                        [style.flag_width / 2],
                        marker=style.team_markers.get(team, style.marker),
                        color=colors[team],
                        linestyle="none",
                    )
                )
            text = TextArea(
                names[team], textprops={"fontsize": style.font_size, "color": style.text_color}
            )
            legend_rows.append(HPacker(children=[icon, text], align="center", pad=0, sep=6))
        legend_box = AnchoredOffsetbox(
            loc="upper left",
            child=VPacker(children=legend_rows, align="left", pad=0, sep=5),
            frameon=True,
            pad=0.4,
            borderpad=0.5,
        )
        legend_box.patch.set_facecolor(style.background)
        legend_box.patch.set_edgecolor(style.grid_color)
        axes.add_artist(legend_box)
    elif style.show_legend and style.color_by_team and request.kind != "histogram":
        names = {p.team_id: p.team_name for p in data.points}
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
        legend = axes.legend(
            handles=handles,
            fontsize=style.font_size,
            facecolor=style.background,
            edgecolor=style.grid_color,
        )
        for legend_text in legend.get_texts():
            legend_text.set_color(style.text_color)
    if style.show_grid:
        axes.set_axisbelow(True)
        axes.grid(
            True,
            color=style.grid_color,
            alpha=0.5,
            axis="both" if request.kind == "scatter" else "x" if request.kind == "bar" else "y",
        )
    axes.set_title(title, color=style.text_color, fontsize=style.font_size + 3, pad=12)
    population = "jogador" if request.group_by == "player" else "equipe"
    grain = (
        f"{population} × partida"
        if request.aggregation == "per_match"
        else ("jogador por equipe" if request.group_by == "player" else "equipe")
        + " · agregado nas partidas selecionadas"
    )
    reviewed = sum(any(v.review_reasons for v in p.values) for p in data.points)
    footer = (
        f"{grain} | partidas-fonte: {len(data.sources)} | {len(data.points)} exibidas | "
        f"{len(data.excluded)} excluídas | {reviewed} com ressalvas"
    )
    if data.omitted_by_top_n:
        footer += f" | {data.omitted_by_top_n} fora do top N"
    if request.size_metric:
        footer += f"\nÁrea dos pontos: escala relativa de {definitions[request.size_metric].name}"
    if style.identity_style == "flag" and flags is not None:
        footer += "\nBandeiras de tamanho fixo centralizadas em (X, Y)"
        if flags.missing:
            footer += f" | equipes sem bandeira: {len(flags.missing)}; símbolo preservado"
    figure.supxlabel(footer, fontsize=max(6.0, style.font_size - 2), color=style.text_color)
    return figure


def write_plot_bundle(
    data: PreparedPlot,
    output_dir: Path,
    *,
    flag_catalogue: Path | None = None,
) -> tuple[Path, Path]:
    """Render before creating a new directory; refuse an existing destination."""
    if output_dir.exists():
        raise FileExistsError(f"Plot output directory already exists: {output_dir}")
    flags = None
    if data.request.style.identity_style == "flag":
        if flag_catalogue is None:
            raise ValueError("Flag mode requires a catalogue path")
        flags = resolve_team_flags(flag_catalogue, tuple(p.team_id for p in data.points))
    figure = render_metric_plot(data, flags=flags)
    buffer = io.BytesIO()
    try:
        figure.savefig(
            buffer,
            format=data.request.output_format,
            dpi=data.request.style.dpi,
            facecolor=data.request.style.background,
        )
    finally:
        figure.clear()
    image = buffer.getvalue()
    image_name = f"plot.{data.request.output_format}"
    metadata = {
        "artifact_version": "1.3.0",
        "image_file": image_name,
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "data": data.model_dump(mode="json"),
    }
    if flags is not None:
        metadata["identity_assets"] = flags.metadata()
    if data.request.kind in {"box", "correlation", "pair", "line"}:
        metadata["analysis"] = advanced_analysis(data)
    encoded = (json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    output_dir.mkdir(parents=True, exist_ok=False)
    image_path, data_path = output_dir / image_name, output_dir / "data.json"
    try:
        with image_path.open("xb") as handle:
            handle.write(image)
        with data_path.open("xb") as handle:
            handle.write(encoded)
    except Exception:
        image_path.unlink(missing_ok=True)
        data_path.unlink(missing_ok=True)
        output_dir.rmdir()
        raise
    return image_path, data_path


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--flags-catalogue", type=Path, default=Path("assets/flags/world-cup-2022/catalogue.json")
    )
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("Plot output directory exists; choose a new destination")
    request = PlotRequest.model_validate_json(args.config.read_bytes())
    data = prepare_metric_plot(args.index, request)
    image_path, data_path = write_plot_bundle(
        data, args.output_dir, flag_catalogue=args.flags_catalogue
    )
    print(
        json.dumps(
            {
                "plot": str(image_path),
                "data": str(data_path),
                "population": data.population_count,
                "displayed": len(data.points),
                "excluded": len(data.excluded),
                "omitted_by_top_n": data.omitted_by_top_n,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
