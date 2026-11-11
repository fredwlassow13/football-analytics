from pathlib import Path
from typing import Literal

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from football_analytics.ingestion.player_pass_heatmap import PlayerPassHeatmap
from football_analytics.visualization.pass_network import _draw_pitch


def render_player_pass_heatmap(
    heatmap: PlayerPassHeatmap,
    *,
    player_name: str,
    team_name: str,
    output_path: Path,
    mode: Literal["count", "percentage"] = "count",
) -> Path:
    if mode not in {"count", "percentage"}:
        raise ValueError("Invalid heatmap mode")
    if output_path.suffix.lower() not in {".png", ".svg"}:
        raise ValueError("Heatmap output must be PNG or SVG")
    if heatmap.columns < 1 or heatmap.rows < 1:
        raise ValueError("Invalid heatmap dimensions")

    expected_cells = [
        (row, column) for row in range(heatmap.rows) for column in range(heatmap.columns)
    ]
    if [(cell.row, cell.column) for cell in heatmap.cells] != expected_cells:
        raise ValueError("Heatmap cells must form an ordered complete grid")

    values = (
        [float(cell.count) for cell in heatmap.cells]
        if mode == "count"
        else list(heatmap.percentages or (0.0,) * len(heatmap.cells))
    )
    matrix = [
        values[row * heatmap.columns : (row + 1) * heatmap.columns] for row in range(heatmap.rows)
    ]

    figure = Figure(figsize=(11, 8), facecolor="white")
    FigureCanvasAgg(figure)
    axes = figure.add_axes((0.07, 0.23, 0.78, 0.59))

    image = axes.imshow(
        matrix,
        extent=(0, 120, 80, 0),
        origin="upper",
        interpolation="nearest",
        cmap="YlOrRd",
        vmin=0,
        vmax=max(max(values, default=0.0), 1.0),
        aspect="equal",
        zorder=0,
    )
    _draw_pitch(axes)

    cell_width = 120 / heatmap.columns
    cell_height = 80 / heatmap.rows

    for column in range(1, heatmap.columns):
        axes.plot(
            [column * cell_width] * 2,
            [0, 80],
            color="white",
            linewidth=0.4,
            alpha=0.5,
        )
    for row in range(1, heatmap.rows):
        axes.plot(
            [0, 120],
            [row * cell_height] * 2,
            color="white",
            linewidth=0.4,
            alpha=0.5,
        )

    maximum = max(values, default=0.0)
    for cell, value in zip(heatmap.cells, values, strict=True):
        if cell.count == 0:
            continue
        label = str(cell.count) if mode == "count" else f"{value:.1f}%"
        axes.text(
            (cell.column + 0.5) * cell_width,
            (cell.row + 0.5) * cell_height,
            label,
            ha="center",
            va="center",
            fontsize=7,
            color="white" if value > maximum * 0.6 else "#0f172a",
        )

    colorbar_axes = figure.add_axes((0.89, 0.28, 0.025, 0.48))
    colorbar = figure.colorbar(image, cax=colorbar_axes)
    colorbar.set_label(
        "Passes tentados por célula"
        if mode == "count"
        else "% dos passes com origem dentro do campo"
    )

    if heatmap.located_count == 0:
        axes.text(
            60,
            40,
            "Sem origens de passe dentro do campo neste recorte",
            ha="center",
            va="center",
            fontsize=10,
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.9},
        )

    figure.suptitle(
        f"Origens de passe · {player_name}",
        fontsize=15,
        fontweight="bold",
        y=0.96,
    )
    figure.text(
        0.5,
        0.89,
        f"{team_name} · períodos {', '.join(map(str, heatmap.periods))}\n"
        f"{heatmap.total_events} passes tentados · "
        f"{heatmap.located_count} dentro do campo · "
        f"{len(heatmap.outside_pitch_event_ids)} fora do campo",
        ha="center",
        fontsize=10,
    )
    figure.text(
        0.5,
        0.10,
        f"Grade {heatmap.columns} × {heatmap.rows} · sem suavização\n"
        "Concentração de ações registradas; não mede tempo de permanência.\n"
        "Percentuais usam somente origens dentro do campo.\n"
        "Fonte: StatsBomb Open Data · coordenadas originais 120 × 80.",
        ha="center",
        fontsize=9,
        color="#475569",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        figure.savefig(output_path, dpi=180, facecolor=figure.get_facecolor())
    finally:
        figure.clear()

    return output_path
