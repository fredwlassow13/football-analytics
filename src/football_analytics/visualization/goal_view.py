from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.transforms import Bbox

from football_analytics.ingestion.action_context import ContextualizedAction
from football_analytics.integrations.base import MatchShot


@dataclass(frozen=True)
class GoalViewPoint:
    event_id: str
    source_index: int
    team_id: str
    player_id: str
    outcome: str
    end_x: float
    end_y: float
    end_z: float

    @property
    def on_goal_plane(self) -> bool:
        return self.end_x == 120.0


def prepare_goal_view(
    actions: Sequence[ContextualizedAction],
) -> list[GoalViewPoint]:
    identities = {(item.action.provider, item.action.external_match_id) for item in actions}

    if len(identities) > 1:
        raise ValueError("Goal view requires one provider and match")

    result: list[GoalViewPoint] = []
    seen_ids: set[str] = set()

    for item in sorted(actions, key=lambda value: value.action.source_index):
        shot = item.action

        if not isinstance(shot, MatchShot):
            continue

        if shot.external_event_id in seen_ids:
            raise ValueError("Duplicate shot event")

        seen_ids.add(shot.external_event_id)

        if shot.period not in {1, 2, 3, 4, 5}:
            raise ValueError("Unsupported shot period")

        if shot.period == 5 or not shot.is_on_target:
            continue

        if shot.external_team_id is None or shot.external_player_id is None:
            raise ValueError("Goal view requires team and player references")

        destination = item.spatial.end_location

        if destination is None or destination.z is None:
            raise ValueError("Goal view requires a destination with height")

        result.append(
            GoalViewPoint(
                event_id=shot.external_event_id,
                source_index=shot.source_index,
                team_id=shot.external_team_id,
                player_id=shot.external_player_id,
                outcome=shot.outcome,
                end_x=destination.x,
                end_y=destination.y,
                end_z=destination.z,
            )
        )

    return result


def render_goal_view(
    actions: Sequence[ContextualizedAction],
    *,
    team_names: Mapping[str, str],
    output_path: Path,
    title: str,
    player_names: Mapping[str, str] | None = None,
) -> Path:
    if len(team_names) != 2:
        raise ValueError("Goal view requires exactly two teams")

    if output_path.suffix.lower() not in {".png", ".svg"}:
        raise ValueError("Goal view output must be PNG or SVG")

    points = prepare_goal_view(actions)

    if any(point.team_id not in team_names for point in points):
        raise ValueError("Goal view contains an unknown team")

    names = player_names if player_names is not None else {}
    colors = dict(zip(team_names, ("#2563eb", "#dc2626"), strict=True))

    on_plane = [point for point in points if point.on_goal_plane]
    outside_plane = [point for point in points if not point.on_goal_plane]

    on_plane_title = "Destinos na linha do gol: x = 120"

    if on_plane and all(point.outcome == "goal" for point in on_plane):
        on_plane_title = "Gols na linha do gol: x = 120"

    outside_title = "Destinos fora do plano do gol: x ≠ 120"

    if outside_plane and all(
        point.end_x < 120 and point.outcome != "goal" for point in outside_plane
    ):
        outside_title = "Defesas antes da linha do gol: x < 120"

    groups = [
        (on_plane_title, on_plane),
        (outside_title, outside_plane),
    ]

    min_y = min([35.1, *(point.end_y - 0.9 for point in points)])
    max_y = max([44.9, *(point.end_y + 0.9 for point in points)])
    max_z = max([3.0, *(point.end_z + 0.5 for point in points)])
    min_z = min([-0.2, *(point.end_z - 0.2 for point in points)])

    figure = Figure(figsize=(14, 8), facecolor="white")
    FigureCanvasAgg(figure)

    for panel, (panel_title, group) in enumerate(groups):
        left = 0.06 + panel * 0.48
        axes = figure.add_axes((left, 0.50, 0.40, 0.30))
        axes.set_facecolor("#f8fafc")

        axes.plot(
            [36, 36, 44, 44],
            [0, 2.67, 2.67, 0],
            color="#334155",
            linewidth=3,
        )
        axes.axhline(0, color="#94a3b8", linewidth=1)

        for number, point in enumerate(group, start=1):
            axes.scatter(
                [point.end_y],
                [point.end_z],
                color=colors[point.team_id],
                marker="*" if point.outcome == "goal" else "o",
                s=170 if point.outcome == "goal" else 65,
                edgecolors="white",
                linewidths=0.7,
                zorder=3,
            )

            offset_x = -8 if point.end_y <= 36.5 or point.end_y >= 43.5 else 6
            alignment = "right" if offset_x < 0 else "left"

            axes.annotate(
                str(number),
                (point.end_y, point.end_z),
                xytext=(offset_x, 8),
                textcoords="offset points",
                ha=alignment,
                fontsize=9,
                fontweight="bold",
                color="#334155",
            )

        axes.set_xlim(min_y, max_y)
        axes.set_ylim(min_z, max_z)
        axes.set_aspect("equal")
        axes.set_xlabel("y — coordenada lateral da fonte", fontsize=9)
        axes.set_ylabel("z — altura na escala da fonte", fontsize=9)
        axes.set_title(f"{panel_title}\n{len(group)} destinos", fontsize=11)
        axes.grid(alpha=0.15)

        table_axes = figure.add_axes((left, 0.13, 0.40, 0.27))
        table_axes.set_axis_off()

        if group:
            rows = [
                [
                    str(number),
                    names.get(point.player_id, "Nome não informado"),
                    str(point.source_index),
                    f"{point.end_x:g}",
                ]
                for number, point in enumerate(group, start=1)
            ]

            table = table_axes.table(
                cellText=rows,
                colLabels=["Nº", "Jogador", "Evento", "x"],
                colWidths=[0.07, 0.63, 0.17, 0.13],
                cellLoc="left",
                colLoc="left",
                bbox=Bbox.from_bounds(0, 0, 1, 1),
            )
            table.auto_set_font_size(False)
            table.set_fontsize(8)

            for (row, _), cell in table.get_celld().items():
                cell.set_edgecolor("#e2e8f0")
                cell.set_linewidth(0.5)

                if row == 0:
                    cell.set_facecolor("#e2e8f0")
                    cell.set_text_props(weight="bold")
                else:
                    cell.set_facecolor("#f8fafc" if row % 2 == 0 else "white")

            for row, point in enumerate(group, start=1):
                table[(row, 0)].set_text_props(
                    color=colors[point.team_id],
                    weight="bold",
                )
        else:
            table_axes.text(
                0.5,
                0.5,
                "Nenhum destino neste recorte.",
                ha="center",
                va="center",
                fontsize=10,
            )

    handles = [
        Line2D(
            [],
            [],
            color=colors[team_id],
            marker="o",
            linestyle="None",
            label=team_name,
        )
        for team_id, team_name in team_names.items()
    ]

    figure.suptitle(title, fontsize=17, fontweight="bold", y=0.97)
    figure.text(
        0.5,
        0.92,
        "Somente chutes no alvo · períodos 1–4 · disputa de pênaltis excluída",
        ha="center",
        fontsize=10,
    )
    figure.legend(
        handles=handles,
        loc="center",
        bbox_to_anchor=(0.5, 0.445),
        ncol=2,
        frameon=False,
    )
    figure.text(
        0.5,
        0.055,
        "À direita: projeção y–z dos destinos de defesa, "
        "sem estimar onde cruzariam a linha do gol.\n"
        "Números identificam os pontos na tabela de cada painel. "
        "Fonte: StatsBomb Open Data.",
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
