from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite, sqrt
from pathlib import Path

from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Arc, Rectangle

from football_analytics.ingestion.action_context import ContextualizedAction
from football_analytics.integrations.base import MatchShot

STYLES = {
    "goal": ("Gol", "#b77900", "*"),
    "saved": ("Defendido", "#2563eb", "o"),
    "saved_to_post": ("Defendido para a trave", "#0891b2", "D"),
    "saved_off_target": ("Defendido, fora do alvo", "#7c3aed", "s"),
    "blocked": ("Bloqueado", "#475569", "X"),
    "off_target": ("Fora", "#dc2626", "^"),
    "post": ("Trave", "#ea580c", "D"),
    "wayward": ("Sem ameaça (Wayward)", "#a21caf", "v"),
}


@dataclass(frozen=True)
class ShotMapPoint:
    event_id: str
    team_id: str
    player_id: str
    x: float
    y: float
    outcome: str
    statsbomb_xg: float | None = None


def prepare_shot_map(
    actions: Sequence[ContextualizedAction],
    *,
    player_id: str | None = None,
) -> list[ShotMapPoint]:
    identities = {(item.action.provider, item.action.external_match_id) for item in actions}

    if len(identities) > 1:
        raise ValueError("Shot map requires one provider and match")

    points: list[ShotMapPoint] = []
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

        if shot.period == 5:
            continue

        if shot.external_team_id is None or shot.external_player_id is None:
            raise ValueError("Shot map requires team and player references")

        if player_id is not None and shot.external_player_id != player_id:
            continue

        location = item.spatial.start_location

        if location is None:
            raise ValueError("Shot map requires a start location")

        points.append(
            ShotMapPoint(
                event_id=shot.external_event_id,
                team_id=shot.external_team_id,
                player_id=shot.external_player_id,
                x=location.x,
                y=location.y,
                outcome=shot.outcome,
                statsbomb_xg=shot.statsbomb_xg,
            )
        )

    return points


def _draw_pitch(axes: Axes) -> None:
    axes.set_facecolor("#f0f6f0")

    for rectangle in (
        Rectangle((0, 0), 120, 80, fill=False),
        Rectangle((102, 18), 18, 44, fill=False),
        Rectangle((114, 30), 6, 20, fill=False),
        Rectangle((120, 36), 2, 8, fill=False),
    ):
        rectangle.set_edgecolor("#64748b")
        rectangle.set_linewidth(1.1)
        axes.add_patch(rectangle)

    axes.plot([60, 60], [0, 80], color="#64748b", linewidth=1.1)
    axes.add_patch(
        Arc(
            (60, 40),
            20,
            20,
            theta1=-90,
            theta2=90,
            color="#64748b",
        )
    )
    axes.add_patch(
        Arc(
            (108, 40),
            20,
            20,
            theta1=126.87,
            theta2=233.13,
            color="#64748b",
        )
    )
    axes.scatter([108], [40], color="#64748b", s=10)

    axes.set_xlim(58, 124)
    axes.set_ylim(82, -2)
    axes.set_aspect("equal")
    axes.set_axis_off()


def render_shot_map(
    actions: Sequence[ContextualizedAction],
    *,
    team_names: Mapping[str, str],
    output_path: Path,
    title: str,
    player_id: str | None = None,
) -> Path:
    if len(team_names) != 2:
        raise ValueError("Shot map requires exactly two teams")

    if output_path.suffix.lower() not in {".png", ".svg"}:
        raise ValueError("Shot map output must be PNG or SVG")

    points = prepare_shot_map(actions, player_id=player_id)

    if any(point.team_id not in team_names for point in points):
        raise ValueError("Shot map contains an unknown team")

    # Usa o campo completo quando houver chutes anteriores à linha central.
    full_pitch = any(point.x < 60 for point in points)

    figure = Figure(figsize=(12, 8), facecolor="#ffffff")
    FigureCanvasAgg(figure)

    for panel, (team_id, team_name) in enumerate(team_names.items(), start=1):
        axes = figure.add_subplot(1, 2, panel)
        _draw_pitch(axes)

        if full_pitch:
            axes.set_xlim(-2, 124)

        team_points = [point for point in points if point.team_id == team_id]

        for outcome, (_, color, marker) in STYLES.items():
            group = [point for point in team_points if point.outcome == outcome]

            if not group:
                continue

            axes.scatter(
                [point.x for point in group],
                [point.y for point in group],
                s=170 if outcome == "goal" else 65,
                color=color,
                marker=marker,
                edgecolors="#ffffff",
                linewidths=0.7,
                alpha=0.9,
                zorder=3,
            )

        goals = sum(point.outcome == "goal" for point in team_points)
        on_target = sum(
            point.outcome in {"goal", "saved", "saved_to_post"} for point in team_points
        )
        axes.set_title(
            f"{team_name}\n{len(team_points)} finalizações · {on_target} no alvo · {goals} gols",
            fontsize=12,
            pad=14,
        )

    present_outcomes = {point.outcome for point in points}
    handles = [
        Line2D(
            [],
            [],
            color=color,
            marker=marker,
            linestyle="None",
            markersize=9,
            label=label,
        )
        for outcome, (label, color, marker) in STYLES.items()
        if outcome in present_outcomes
    ]

    figure.suptitle(title, fontsize=18, fontweight="bold", y=0.97)
    figure.text(
        0.5,
        0.91,
        "Períodos 1–4, incluindo prorrogação · posições iniciais dos chutes",
        ha="center",
        fontsize=10,
        color="#475569",
    )

    if handles:
        figure.legend(
            handles=handles,
            loc="lower center",
            bbox_to_anchor=(0.5, 0.065),
            ncol=min(4, len(handles)),
            frameon=False,
        )

    figure.text(
        0.5,
        0.025,
        "Fonte: StatsBomb Open Data · coordenadas originais 120 × 80\n"
        "Marcadores de tamanho fixo; ações coincidentes podem se sobrepor.",
        ha="center",
        fontsize=9,
        color="#475569",
    )
    figure.subplots_adjust(top=0.82, bottom=0.20, wspace=0.15)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        figure.savefig(output_path, dpi=180, facecolor=figure.get_facecolor())
    finally:
        figure.clear()

    return output_path


def xg_marker_area(xg: float | None) -> float:
    """Return scatter marker area in points squared."""

    if xg is None:
        raise ValueError("Marker area requires an observed xG value")

    if isinstance(xg, bool) or not isfinite(xg) or not 0 <= xg <= 1:
        raise ValueError("Invalid xG probability")

    return 1000.0 * xg


def render_xg_shot_map(
    actions: Sequence[ContextualizedAction],
    *,
    team_names: Mapping[str, str],
    output_path: Path,
    title: str,
    player_id: str | None = None,
) -> Path:
    if len(team_names) != 2:
        raise ValueError("xG shot map requires exactly two teams")

    if output_path.suffix.lower() not in {".png", ".svg"}:
        raise ValueError("xG shot map output must be PNG or SVG")

    points = prepare_shot_map(actions, player_id=player_id)

    if any(point.team_id not in team_names for point in points):
        raise ValueError("xG shot map contains an unknown team")

    areas = {point.event_id: xg_marker_area(point.statsbomb_xg) for point in points}

    figure = Figure(figsize=(12, 8), facecolor="white")
    FigureCanvasAgg(figure)

    for panel, (team_id, team_name) in enumerate(team_names.items(), start=1):
        axes = figure.add_subplot(1, 2, panel)
        _draw_pitch(axes)

        if any(point.x < 60 for point in points):
            axes.set_xlim(-2, 124)

        team_points = [point for point in points if point.team_id == team_id]

        for outcome, (_, color, _) in STYLES.items():
            positive = [
                point
                for point in team_points
                if point.outcome == outcome and areas[point.event_id] > 0
            ]
            zero = [
                point
                for point in team_points
                if point.outcome == outcome and areas[point.event_id] == 0
            ]

            if positive:
                axes.scatter(
                    [point.x for point in positive],
                    [point.y for point in positive],
                    s=[areas[point.event_id] for point in positive],
                    color=color,
                    marker="o",
                    edgecolors="white",
                    linewidths=0.8,
                    alpha=0.85,
                    zorder=3,
                )

            if zero:
                axes.scatter(
                    [point.x for point in zero],
                    [point.y for point in zero],
                    s=30,
                    color=color,
                    marker="+",
                    linewidths=1.5,
                    zorder=4,
                )

        coordinate_counts = Counter((point.x, point.y) for point in team_points)
        coincidences = sorted(
            ((coordinate, count) for coordinate, count in coordinate_counts.items() if count > 1),
            key=lambda item: (item[0][1], item[0][0]),
        )

        for index, ((x, y), count) in enumerate(coincidences):
            label_y = 1 - (index + 1) / (len(coincidences) + 1)

            axes.annotate(
                f"×{count}",
                xy=(x, y),
                xycoords="data",
                xytext=(1.04, label_y),
                textcoords="axes fraction",
                ha="left",
                va="center",
                fontsize=9,
                fontweight="bold",
                color="#334155",
                bbox={
                    "boxstyle": "round,pad=0.25",
                    "facecolor": "white",
                    "edgecolor": "#94a3b8",
                },
                arrowprops={
                    "arrowstyle": "-",
                    "color": "#64748b",
                    "linewidth": 0.8,
                    "shrinkB": 4,
                    "clip_on": False,
                },
                annotation_clip=False,
                clip_on=False,
                zorder=5,
            )

        total_xg = sum(
            (
                Decimal(str(point.statsbomb_xg))
                for point in team_points
                if point.statsbomb_xg is not None
            ),
            Decimal(0),
        )
        goals = sum(point.outcome == "goal" for point in team_points)

        axes.set_title(
            f"{team_name}\n{len(team_points)} chutes · {goals} gols · {total_xg:.3f} xG",
            fontsize=12,
            pad=14,
        )

    present_outcomes = {point.outcome for point in points}
    outcome_handles = [
        Line2D(
            [],
            [],
            color=color,
            marker="o",
            linestyle="None",
            markersize=8,
            label=label,
        )
        for outcome, (label, color, _) in STYLES.items()
        if outcome in present_outcomes
    ]

    if outcome_handles:
        figure.legend(
            handles=outcome_handles,
            loc="lower center",
            bbox_to_anchor=(0.5, 0.14),
            ncol=min(4, len(outcome_handles)),
            frameon=False,
        )

    size_handles = [
        Line2D(
            [],
            [],
            marker="o",
            linestyle="None",
            markerfacecolor="none",
            markeredgecolor="#475569",
            markersize=sqrt(xg_marker_area(value)),
            label=f"xG {value:.2f}".replace(".", ","),
        )
        for value in (0.05, 0.20, 0.50)
    ]

    if any(point.statsbomb_xg == 0 for point in points):
        size_handles.append(
            Line2D(
                [],
                [],
                color="#475569",
                marker="+",
                linestyle="None",
                markersize=7,
                label="xG zero",
            )
        )

    figure.legend(
        handles=size_handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.065),
        ncol=len(size_handles),
        frameon=False,
        handleheight=2.2,
    )

    figure.suptitle(title, fontsize=17, fontweight="bold", y=0.97)
    figure.text(
        0.5,
        0.91,
        "Períodos 1–4 · pênaltis da partida incluídos · disputa excluída",
        ha="center",
        fontsize=10,
        color="#475569",
    )
    figure.text(
        0.5,
        0.015,
        "Área dos círculos proporcional ao xG; cores indicam o resultado.\n"
        "×N indica N chutes na mesma coordenada. "
        "Fonte e modelo de xG: StatsBomb Open Data.",
        ha="center",
        fontsize=9,
        color="#475569",
    )
    figure.subplots_adjust(
        left=0.06,
        right=0.91,
        top=0.82,
        bottom=0.27,
        wspace=0.35,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        figure.savefig(output_path, dpi=180, facecolor=figure.get_facecolor())
    finally:
        figure.clear()

    return output_path
