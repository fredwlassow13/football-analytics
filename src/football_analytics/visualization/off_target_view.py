from collections.abc import Mapping, Sequence
from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.transforms import Bbox

from football_analytics.ingestion.action_context import ContextualizedAction
from football_analytics.integrations.base import MatchShot
from football_analytics.visualization.goal_view import GoalViewPoint


def prepare_off_target_view(
    actions: Sequence[ContextualizedAction],
) -> list[GoalViewPoint]:
    identities = {(item.action.provider, item.action.external_match_id) for item in actions}

    if len(identities) > 1:
        raise ValueError("Off-target view requires one provider and match")

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

        if shot.period == 5 or shot.outcome != "off_target":
            continue

        if shot.external_team_id is None or shot.external_player_id is None:
            raise ValueError("Off-target view requires team and player references")

        destination = item.spatial.end_location

        if destination is None or destination.z is None:
            raise ValueError("Off-target view requires a destination with height")

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


def render_off_target_view(
    actions: Sequence[ContextualizedAction],
    *,
    team_names: Mapping[str, str],
    player_names: Mapping[str, str],
    output_path: Path,
    title: str,
) -> Path:
    if len(team_names) != 2:
        raise ValueError("Off-target view requires exactly two teams")

    if output_path.suffix.lower() not in {".png", ".svg"}:
        raise ValueError("Off-target view output must be PNG or SVG")

    points = prepare_off_target_view(actions)

    if any(point.team_id not in team_names for point in points):
        raise ValueError("Off-target view contains an unknown team")

    min_y = min([35.0, *(point.end_y - 1.0 for point in points)])
    max_y = max([45.0, *(point.end_y + 1.0 for point in points)])
    min_z = min([-0.3, *(point.end_z - 0.3 for point in points)])
    max_z = max([3.2, *(point.end_z + 0.8 for point in points)])

    colors = ("#2563eb", "#dc2626")
    max_rows = max(
        (sum(point.team_id == team_id for point in points) for team_id in team_names),
        default=0,
    )

    figure = Figure(figsize=(14, 8), facecolor="white")
    FigureCanvasAgg(figure)

    for panel, ((team_id, team_name), color) in enumerate(
        zip(team_names.items(), colors, strict=True)
    ):
        group = [point for point in points if point.team_id == team_id]
        left = 0.06 + panel * 0.48
        axes = figure.add_axes((left, 0.49, 0.40, 0.32))
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
                color=color,
                marker="^",
                s=90,
                edgecolors="white",
                linewidths=0.7,
                zorder=3,
            )
            axes.annotate(
                str(number),
                (point.end_y, point.end_z),
                xytext=(6, 8),
                textcoords="offset points",
                fontsize=9,
                fontweight="bold",
                color="#334155",
            )

        axes.set_xlim(min_y, max_y)
        axes.set_ylim(min_z, max_z)
        axes.set_aspect("equal")
        axes.set_xlabel("y — coordenada lateral da fonte", fontsize=9)
        axes.set_ylabel("z — altura na escala da fonte", fontsize=9)
        axes.set_title(
            f"{team_name}\n{len(group)} finalizações Off T",
            fontsize=12,
        )
        axes.grid(alpha=0.15)

        table_axes = figure.add_axes((left, 0.14, 0.40, 0.24))
        table_axes.set_axis_off()

        if group:
            rows = [
                [
                    str(number),
                    player_names.get(point.player_id, "Nome não informado"),
                    str(point.source_index),
                    f"{point.end_x:g}",
                ]
                for number, point in enumerate(group, start=1)
            ]

            height = (len(group) + 1) / (max_rows + 1)
            table = table_axes.table(
                cellText=rows,
                colLabels=["Nº", "Jogador", "Evento", "x"],
                colWidths=[0.07, 0.63, 0.17, 0.13],
                cellLoc="left",
                colLoc="left",
                bbox=Bbox.from_bounds(0, 1 - height, 1, height),
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

            for row in range(1, len(group) + 1):
                table[(row, 0)].set_text_props(color=color, weight="bold")
        else:
            table_axes.text(
                0.5,
                0.8,
                "Nenhuma finalização Off T neste recorte.",
                ha="center",
                fontsize=10,
            )

    figure.suptitle(title, fontsize=17, fontweight="bold", y=0.97)
    figure.text(
        0.5,
        0.92,
        "Resultado Off T · períodos 1–4 · mesma escala nos dois painéis",
        ha="center",
        fontsize=10,
    )
    figure.text(
        0.5,
        0.055,
        "Destinos registrados em projeção y–z; a tabela preserva x. "
        "Não há extrapolação da trajetória.\n"
        "Bloqueados, Wayward e disputa de pênaltis excluídos. "
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
