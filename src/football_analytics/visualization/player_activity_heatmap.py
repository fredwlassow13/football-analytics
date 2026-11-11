from pathlib import Path
from typing import Literal

from matplotlib import colormaps
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import BoundaryNorm, Colormap, ListedColormap, Normalize, to_hex
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from matplotlib.transforms import Bbox

from football_analytics.ingestion.player_activity_heatmap import (
    PlayerActivityHeatmap,
    build_player_activity_heatmap,
)
from football_analytics.visualization.pass_network import _draw_pitch

EVENT_LABELS = {
    "50/50": "Disputas 50/50",
    "Ball Receipt*": "Recepções",
    "Ball Recovery": "Recuperações",
    "Block": "Bloqueios",
    "Carry": "Conduções",
    "Clearance": "Cortes",
    "Dispossessed": "Desarmes sofridos",
    "Dribble": "Dribles tentados",
    "Dribbled Past": "Superado por drible",
    "Duel": "Duelos",
    "Foul Committed": "Faltas cometidas",
    "Foul Won": "Faltas sofridas",
    "Goal Keeper": "Ações de goleiro",
    "Interception": "Interceptações",
    "Miscontrol": "Erros de controle",
    "Offside": "Impedimentos",
    "Pass": "Passes tentados",
    "Pressure": "Pressões",
    "Shield": "Proteções de bola",
    "Shot": "Finalizações",
}


def render_player_activity_heatmap(
    heatmap: PlayerActivityHeatmap,
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

    # Confere se as células ainda correspondem à seleção original.
    reference = build_player_activity_heatmap(
        heatmap.selection,
        columns=heatmap.columns,
        rows=heatmap.rows,
    )
    if heatmap.cells != reference.cells:
        raise ValueError("Heatmap grid differs from source selection")

    values = (
        [float(cell.count) for cell in heatmap.cells]
        if mode == "count"
        else list(heatmap.percentages or (0.0,) * len(heatmap.cells))
    )
    matrix = [
        values[row * heatmap.columns : (row + 1) * heatmap.columns] for row in range(heatmap.rows)
    ]
    maximum = max(values, default=0.0)
    cmap: Colormap = colormaps["YlOrRd"]
    norm: Normalize = Normalize(vmin=0, vmax=max(maximum, 1.0))
    if mode == "count":
        highest_count = max(int(maximum), 1)
        colors = ["#f1f5f9"] + [
            to_hex(colormaps["YlOrRd"](0.3 + 0.7 * index / max(highest_count - 1, 1)))
            for index in range(highest_count)
        ]
        cmap = ListedColormap(colors)
        norm = BoundaryNorm(
            [value - 0.5 for value in range(highest_count + 2)],
            ncolors=cmap.N,
        )

    figure = Figure(figsize=(14, 9), facecolor="white")
    FigureCanvasAgg(figure)
    axes = figure.add_axes((0.04, 0.25, 0.61, 0.57))

    image = axes.imshow(
        matrix,
        extent=(0, 120, 80, 0),
        origin="upper",
        interpolation="nearest",
        cmap=cmap,
        norm=norm,
        aspect="equal",
        zorder=0,
    )
    _draw_pitch(axes)

    width = 120 / heatmap.columns
    height = 80 / heatmap.rows

    for column in range(1, heatmap.columns):
        axes.plot(
            [column * width] * 2,
            [0, 80],
            color="white",
            linewidth=0.4,
            alpha=0.5,
        )
    for row in range(1, heatmap.rows):
        axes.plot(
            [0, 120],
            [row * height] * 2,
            color="white",
            linewidth=0.4,
            alpha=0.5,
        )

    for cell, value in zip(heatmap.cells, values, strict=True):
        if not cell.count:
            continue
        axes.text(
            (cell.column + 0.5) * width,
            (cell.row + 0.5) * height,
            str(cell.count) if mode == "count" else f"{value:.1f}%",
            ha="center",
            va="center",
            fontsize=7,
            color="white" if value > maximum * 0.6 else "#0f172a",
        )

    colorbar_axes = figure.add_axes((0.67, 0.32, 0.015, 0.43))
    colorbar = figure.colorbar(image, cax=colorbar_axes)
    colorbar.set_label(
        "Ações por célula" if mode == "count" else "% das ações selecionadas",
        fontsize=9,
    )
    if mode == "count":
        colorbar.locator = MaxNLocator(integer=True)
        colorbar.update_ticks()

    table_axes = figure.add_axes((0.76, 0.25, 0.22, 0.55))
    table_axes.set_axis_off()
    table_axes.set_title("Composição da amostra", fontsize=11, pad=12)

    counts = sorted(
        heatmap.counts_by_type.items(),
        key=lambda item: (-item[1], item[0]),
    )
    table = table_axes.table(
        cellText=[
            [EVENT_LABELS.get(event_type, event_type), str(count)] for event_type, count in counts
        ]
        + [["Total", str(heatmap.total_events)]],
        colLabels=["Ação", "Eventos"],
        colWidths=[0.77, 0.23],
        cellLoc="left",
        colLoc="left",
        bbox=Bbox.from_bounds(0, 0, 1, 1),
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)

    for (row, _), table_cell in table.get_celld().items():
        table_cell.set_edgecolor("#e2e8f0")
        table_cell.set_linewidth(0.4)
        table_cell.set_facecolor("#e2e8f0" if row == 0 else "#f8fafc" if row % 2 == 0 else "white")
        if row in {0, len(counts) + 1}:
            table_cell.set_text_props(weight="bold")

    if heatmap.total_events == 0:
        axes.text(
            60,
            40,
            "Sem ações selecionadas neste recorte",
            ha="center",
            va="center",
            bbox={"facecolor": "white", "edgecolor": "none"},
        )

    figure.suptitle(
        f"Mapa de ações · {player_name}",
        fontsize=17,
        fontweight="bold",
        y=0.96,
    )
    figure.text(
        0.5,
        0.89,
        f"{team_name} · períodos "
        f"{', '.join(map(str, heatmap.selection.periods))} · "
        f"{heatmap.total_events} ações registradas",
        ha="center",
        fontsize=11,
    )
    figure.text(
        0.5,
        0.10,
        f"Grade {heatmap.columns} × {heatmap.rows} · sem suavização\n"
        "Um ponto inicial por evento; uma sequência pode gerar várias ações.\n"
        "Não representa todos os deslocamentos nem o tempo em cada região.\n"
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
