from collections.abc import Sequence
from math import ceil, isfinite
from pathlib import Path
from textwrap import fill
from typing import Literal

import numpy as np
from matplotlib import colormaps
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import BoundaryNorm, Normalize
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from football_analytics.ingestion.defensive_map import DefensiveMap, defensive_grid
from football_analytics.visualization.pass_network import _draw_pitch
from football_analytics.visualization.player_activity_heatmap import EVENT_LABELS

MAP_LABELS = EVENT_LABELS | {
    "Pass": "Passes com vitória aérea",
    "Shot": "Finalizações com vitória aérea",
    "Miscontrol": "Erros de controle com vitória aérea",
}

GROUP_LABELS = {
    "all": "Ações e disputas · todas as fontes únicas",
    "recoveries": "Interceptações e recuperações",
    "duels": "Duelos registrados",
    "pressure": "Pressões e dribles sofridos",
    "blocks_clearances": "Bloqueios e cortes",
    "fouls": "Faltas cometidas e sofridas",
    "aerials": "Resultados aéreos registrados",
}


def render_defensive_maps(
    maps: Sequence[DefensiveMap],
    *,
    labels: Sequence[str],
    output_path: Path,
    title: str = "Mapas defensivos",
    mode: Literal["count", "percentage"] = "count",
    scale_max: float | None = None,
) -> Path:
    if not maps or len(maps) > 4 or len(labels) != len(maps):
        raise ValueError("Provide one to four maps with matching labels")
    if output_path.suffix.lower() not in {".png", ".svg"}:
        raise ValueError("Defensive map output must be PNG or SVG")
    if mode not in {"count", "percentage"}:
        raise ValueError("Invalid defensive map mode")
    scopes = {(m.provider, m.external_match_id, m.events_sha256, m.columns, m.rows) for m in maps}
    if len(scopes) != 1:
        raise ValueError("Comparisons require the same source and grid")
    values_by_map: list[list[float]] = []
    for item in maps:
        if item.cells != defensive_grid(item.points, columns=item.columns, rows=item.rows):
            raise ValueError("Defensive grid differs from source points")
        point_ids = [p.external_event_id for p in item.points]
        issue_ids = [i.external_event_id for i in item.location_issues]
        if (
            len(set(item.candidate_event_ids)) != item.candidate_count
            or len(set(issue_ids)) != len(issue_ids)
            or set(point_ids).intersection(issue_ids)
            or set(point_ids).union(issue_ids) != set(item.candidate_event_ids)
            or sum(item.candidate_counts_by_type.values()) != item.candidate_count
        ):
            raise ValueError("Defensive map coverage differs from source selection")
        if any(
            (p.provider, p.external_match_id, p.external_team_id)
            != (item.provider, item.external_match_id, item.external_team_id)
            or p.period not in item.periods
            or (
                item.external_player_id is not None
                and p.external_player_id != item.external_player_id
            )
            for p in item.points
        ):
            raise ValueError("Defensive point belongs to another scope")
        values_by_map.append(
            [float(cell.count) for cell in item.cells]
            if mode == "count"
            else list(item.percentages or (0.0,) * len(item.cells))
        )
    observed_max = max((value for values in values_by_map for value in values), default=0)
    if scale_max is not None and (
        not isfinite(scale_max) or scale_max <= 0 or scale_max < observed_max
    ):
        raise ValueError("Shared scale must be finite, positive and cover all values")
    maximum = max(scale_max if scale_max is not None else observed_max, 1)
    if mode == "count":
        maximum = ceil(maximum)
    cmap = colormaps["YlOrRd"].with_extremes(bad="#edf1f5")
    norm: Normalize = (
        BoundaryNorm(np.arange(0.5, maximum + 1.5), cmap.N)
        if mode == "count" and maximum <= cmap.N
        else Normalize(0, maximum)
    )
    columns = 1 if len(maps) == 1 else 2
    rows = ceil(len(maps) / columns)
    figure = Figure(figsize=(9 * columns, 9 if rows == 1 else 6 * rows + 1.7), facecolor="white")
    FigureCanvasAgg(figure)
    grid = figure.add_gridspec(
        rows,
        columns,
        left=0.035,
        right=0.89,
        bottom=0.29 if rows == 1 else 0.17,
        top=0.80 if rows == 1 else 0.84,
        wspace=0.14,
        hspace=0.55,
    )
    try:
        for index, (item, values, label) in enumerate(
            zip(maps, values_by_map, labels, strict=True)
        ):
            axes = figure.add_subplot(grid[index // columns, index % columns])
            matrix = np.array(values).reshape(item.rows, item.columns)
            im = axes.imshow(
                np.ma.masked_equal(matrix, 0),
                extent=(0, 120, 80, 0),
                origin="upper",
                interpolation="nearest",
                cmap=cmap,
                norm=norm,
                aspect="equal",
                zorder=0,
            )
            _draw_pitch(axes)
            width, height = 120 / item.columns, 80 / item.rows
            for x in range(1, item.columns):
                axes.plot([x * width] * 2, [0, 80], color="white", lw=0.4, alpha=0.65)
            for y in range(1, item.rows):
                axes.plot([0, 120], [y * height] * 2, color="white", lw=0.4, alpha=0.65)
            for cell, value in zip(item.cells, values, strict=True):
                if not cell.count:
                    continue
                axes.text(
                    (cell.column + 0.5) * width,
                    (cell.row + 0.5) * height,
                    str(cell.count) if mode == "count" else f"{value:.1f}%",
                    ha="center",
                    va="center",
                    fontsize=7,
                    color="white" if value > maximum * 0.6 else "#152b40",
                )
            selected_label = (
                ", ".join(MAP_LABELS.get(t, t) for t in item.event_types)
                if item.event_types
                else GROUP_LABELS[item.group]
            )
            axes.set_title(
                f"{label} · períodos {', '.join(map(str, item.periods))}\n{selected_label}",
                loc="left",
                fontsize=11,
                fontweight="bold",
                pad=14,
            )
            composition = ", ".join(
                f"{MAP_LABELS.get(t, t)}: {n}" for t, n in item.candidate_counts_by_type.items()
            )
            axes.text(
                0,
                -0.09,
                f"{item.located_count}/{item.candidate_count} eventos localizados · "
                f"{len(item.location_issues)} sem posição utilizável\n"
                + fill(composition or "Sem eventos selecionados", 85),
                transform=axes.transAxes,
                va="top",
                fontsize=8,
                color="#405369",
            )
            if item.located_count == 0:
                axes.text(
                    60,
                    40,
                    "Sem posições utilizáveis neste recorte",
                    ha="center",
                    va="center",
                    bbox={"facecolor": "white", "edgecolor": "none"},
                    fontsize=10,
                )
        colorbar_axes = figure.add_axes((0.925, 0.29, 0.014, 0.40))
        colorbar = figure.colorbar(im, cax=colorbar_axes)
        colorbar.set_label(
            "Eventos por célula" if mode == "count" else "% dos eventos localizados", fontsize=10
        )
        if mode == "count":
            colorbar.locator = MaxNLocator(integer=True)
            colorbar.update_ticks()
        figure.suptitle(
            title, x=0.035, ha="left", y=0.97, fontsize=20, fontweight="bold", color="#152b40"
        )
        figure.text(
            0.035,
            0.91,
            "Escala e grade comuns entre painéis · zero em cinza · sem suavização",
            fontsize=11,
            color="#405369",
        )
        figure.text(
            0.035,
            0.045,
            f"Grade {maps[0].columns} × {maps[0].rows} · "
            "um ponto inicial por evento de origem, sem duplicação.\n"
            "Distribuição de registros; não mede sucesso defensivo, "
            "posse ou tempo em cada região.\n"
            "Coordenadas originais 120 × 80, sem espelhamento. Fonte: StatsBomb Open Data.",
            fontsize=9,
            color="#405369",
            linespacing=1.6,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(output_path, dpi=160, facecolor="white")
    finally:
        figure.clear()
    return output_path
