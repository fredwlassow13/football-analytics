from collections.abc import Mapping, Sequence
from math import hypot
from pathlib import Path

from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Arc, Circle, FancyArrowPatch, Rectangle
from matplotlib.transforms import Bbox

from football_analytics.ingestion.pass_network import PassConnection
from football_analytics.ingestion.pass_network_nodes import PassNetworkNode


def _draw_pitch(axes: Axes) -> None:
    for x, y, width, height in (
        (0, 0, 120, 80),
        (0, 18, 18, 44),
        (102, 18, 18, 44),
        (0, 30, 6, 20),
        (114, 30, 6, 20),
        (-2, 36, 2, 8),
        (120, 36, 2, 8),
    ):
        axes.add_patch(
            Rectangle(
                (x, y),
                width,
                height,
                fill=False,
                edgecolor="#94a3b8",
                linewidth=0.9,
            )
        )

    axes.plot([60, 60], [0, 80], color="#94a3b8", linewidth=0.9)
    axes.add_patch(Circle((60, 40), 10, fill=False, edgecolor="#94a3b8"))
    axes.add_patch(Arc((12, 40), 20, 20, theta1=-53.13, theta2=53.13, color="#94a3b8"))
    axes.add_patch(Arc((108, 40), 20, 20, theta1=126.87, theta2=233.13, color="#94a3b8"))
    axes.scatter([12, 60, 108], [40, 40, 40], s=5, color="#94a3b8")
    axes.set_xlim(-4, 124)
    axes.set_ylim(84, -4)
    axes.set_aspect("equal")
    axes.set_axis_off()


def render_pass_network(
    nodes: Sequence[PassNetworkNode],
    connections: Sequence[PassConnection],
    *,
    team_names: Mapping[str, str],
    player_names: Mapping[str, str],
    shirt_numbers: Mapping[tuple[str, str], int],
    output_path: Path,
    title: str,
    min_completed_passes: int = 5,
    window_label: str | None = None,
    nominal_player_ids: Mapping[str, Sequence[str]] | None = None,
) -> Path:
    if len(team_names) != 2:
        raise ValueError("Pass network requires exactly two teams")

    if isinstance(min_completed_passes, bool) or min_completed_passes < 1:
        raise ValueError("Minimum completed passes must be positive")

    if output_path.suffix.lower() not in {".png", ".svg"}:
        raise ValueError("Pass network output must be PNG or SVG")

    network_items: list[PassNetworkNode | PassConnection] = [
        *nodes,
        *connections,
    ]
    identities = {(item.provider, item.external_match_id) for item in network_items}

    if len(identities) > 1:
        raise ValueError("Pass network requires one provider and match")

    by_player = {(node.external_team_id, node.external_player_id): node for node in nodes}

    if len(by_player) != len(nodes):
        raise ValueError("Duplicate network node")

    if any(item.external_team_id not in team_names for item in network_items):
        raise ValueError("Pass network contains an unknown team")

    if nominal_player_ids is None:
        roster = {
            team_id: tuple(
                node.external_player_id for node in nodes if node.external_team_id == team_id
            )
            for team_id in team_names
        }
    else:
        if set(nominal_player_ids) != set(team_names):
            raise ValueError("Nominal roster must contain exactly the displayed teams")

        roster = {team_id: tuple(player_ids) for team_id, player_ids in nominal_player_ids.items()}

        all_players = [player_id for player_ids in roster.values() for player_id in player_ids]
        if len(all_players) != len(set(all_players)):
            raise ValueError("Duplicate nominal player")

        for player_ids in roster.values():
            if not player_ids or len(player_ids) > 11:
                raise ValueError("Nominal roster must contain 1 to 11 players")
            if any(not player_id for player_id in player_ids):
                raise ValueError("Invalid nominal player")

        if any(node.external_player_id not in roster[node.external_team_id] for node in nodes):
            raise ValueError("Network node is outside nominal roster")

    for team_id, player_ids in roster.items():
        numbers = []

        for player_id in player_ids:
            key = (team_id, player_id)
            if key not in shirt_numbers:
                raise ValueError("Missing shirt number for network player")

            number = shirt_numbers[key]
            if not isinstance(number, int) or isinstance(number, bool) or number < 0:
                raise ValueError("Invalid shirt number")

            numbers.append(number)

        if len(numbers) != len(set(numbers)):
            raise ValueError("Duplicate shirt number within team")

    for connection in connections:
        sender = by_player.get(
            (
                connection.external_team_id,
                connection.external_sender_player_id,
            )
        )
        recipient = by_player.get(
            (
                connection.external_team_id,
                connection.external_recipient_player_id,
            )
        )

        if nominal_player_ids is not None:
            player_ids = roster[connection.external_team_id]
            if (
                connection.external_sender_player_id not in player_ids
                or connection.external_recipient_player_id not in player_ids
            ):
                raise ValueError("Connection is outside nominal roster")

        if sender is None or recipient is None:
            if nominal_player_ids is None:
                raise ValueError("Connection references a missing node")
            continue

        if connection.completed_passes >= min_completed_passes and (
            sender.mean_x,
            sender.mean_y,
        ) == (recipient.mean_x, recipient.mean_y):
            raise ValueError("Coincident connected nodes require layout review")

    figure = Figure(figsize=(14, 11), facecolor="white")
    FigureCanvasAgg(figure)
    colors = ("#2563eb", "#dc2626")

    for panel, ((team_id, team_name), color) in enumerate(
        zip(team_names.items(), colors, strict=True)
    ):
        team_nodes = sorted(
            (node for node in nodes if node.external_team_id == team_id),
            key=lambda node: (
                shirt_numbers[(team_id, node.external_player_id)],
                node.external_player_id,
            ),
        )
        team_connections = [item for item in connections if item.external_team_id == team_id]
        positioned = [
            item
            for item in team_connections
            if (team_id, item.external_sender_player_id) in by_player
            and (team_id, item.external_recipient_player_id) in by_player
        ]
        visible = [item for item in positioned if item.completed_passes >= min_completed_passes]
        unpositioned_passes = sum(item.completed_passes for item in team_connections) - sum(
            item.completed_passes for item in positioned
        )

        team_players = sorted(
            roster[team_id],
            key=lambda player_id: (
                shirt_numbers[(team_id, player_id)],
                player_id,
            ),
        )
        total_passes = sum(item.completed_passes for item in team_connections)
        visible_passes = sum(item.completed_passes for item in visible)

        left = 0.05 + panel * 0.49
        axes = figure.add_axes((left, 0.51, 0.42, 0.33))
        _draw_pitch(axes)

        for connection in sorted(visible, key=lambda item: item.completed_passes):
            sender = by_player[
                (
                    team_id,
                    connection.external_sender_player_id,
                )
            ]
            recipient = by_player[
                (
                    team_id,
                    connection.external_recipient_player_id,
                )
            ]

            axes.add_patch(
                FancyArrowPatch(
                    (sender.mean_x, sender.mean_y),
                    (recipient.mean_x, recipient.mean_y),
                    arrowstyle="-|>",
                    connectionstyle="arc3,rad=0.12",
                    mutation_scale=9,
                    shrinkA=5,
                    shrinkB=5,
                    linewidth=0.12 * connection.completed_passes,
                    color=color,
                    alpha=0.55,
                    zorder=2,
                )
            )

        for node in team_nodes:
            number = shirt_numbers[(team_id, node.external_player_id)]
            axes.scatter(
                [node.mean_x],
                [node.mean_y],
                s=60,
                color=color,
                edgecolors="white",
                linewidths=0.8,
                zorder=3,
            )

            others = [
                other for other in team_nodes if other.external_player_id != node.external_player_id
            ]
            offset_x, offset_y = 5, 7

            if others:
                nearest = min(
                    others,
                    key=lambda other: hypot(
                        node.mean_x - other.mean_x,
                        node.mean_y - other.mean_y,
                    ),
                )
                if (
                    hypot(
                        node.mean_x - nearest.mean_x,
                        node.mean_y - nearest.mean_y,
                    )
                    < 8
                ):
                    offset_x = 10 if node.mean_x >= nearest.mean_x else -10
                    offset_y = -10 if node.mean_y >= nearest.mean_y else 10

            axes.annotate(
                str(number),
                (node.mean_x, node.mean_y),
                xytext=(offset_x, offset_y),
                textcoords="offset points",
                ha="left" if offset_x > 0 else "right",
                fontsize=8,
                fontweight="bold",
                color="#0f172a",
            )

        axes.set_title(
            f"{team_name} · {len(team_players)} jogadores na tabela\n"
            f"{len(team_nodes)} com posição média · "
            f"{len(visible)}/{len(team_connections)} conexões exibidas "
            f"(mínimo {min_completed_passes})\n"
            f"{visible_passes}/{total_passes} passes completos exibidos · "
            f"{unpositioned_passes} sem coordenadas para ligar os jogadores",
            fontsize=9,
            pad=12,
        )

        if not visible:
            message = (
                "Sem passes tentados neste trecho"
                if not team_nodes
                else "Sem conexões exibíveis com este filtro"
            )
            axes.text(
                0.5,
                -0.08,
                message,
                transform=axes.transAxes,
                ha="center",
                va="top",
                fontsize=8,
                color="#475569",
                clip_on=False,
            )

        table_axes = figure.add_axes((left, 0.09, 0.42, 0.32))
        table_axes.set_axis_off()

        if team_players:
            table = table_axes.table(
                cellText=[
                    [
                        str(shirt_numbers[(team_id, player_id)]),
                        player_names.get(player_id, "Nome não informado"),
                        (
                            str(by_player[(team_id, player_id)].pass_attempts)
                            if (team_id, player_id) in by_player
                            else "0"
                        ),
                    ]
                    for player_id in team_players
                ],
                colLabels=["Camisa", "Jogador", "Passes tentados"],
                colWidths=[0.10, 0.65, 0.25],
                cellLoc="left",
                colLoc="left",
                bbox=Bbox.from_bounds(0, 0, 1, 1),
            )
            table.auto_set_font_size(False)
            table.set_fontsize(7.5)

            for (row, _), cell in table.get_celld().items():
                cell.set_edgecolor("#e2e8f0")
                cell.set_linewidth(0.4)
                cell.set_facecolor(
                    "#e2e8f0" if row == 0 else "#f8fafc" if row % 2 == 0 else "white"
                )
                if row == 0:
                    cell.set_text_props(weight="bold")

    figure.suptitle(title, fontsize=17, fontweight="bold", y=0.98)
    scope_description = (
        window_label if window_label is not None else "Rede agregada da partida · períodos 1–4"
    )
    figure.text(
        0.5,
        0.93,
        f"{scope_description}\nPosições médias das origens de todos os passes tentados no recorte",
        ha="center",
        va="top",
        fontsize=10,
    )
    figure.legend(
        handles=[
            Line2D(
                [],
                [],
                color="#475569",
                linewidth=0.12 * count,
                label=f"{count} passes completos",
            )
            for count in (5, 10, 15)
        ],
        loc="center",
        bbox_to_anchor=(0.5, 0.455),
        ncol=3,
        frameon=False,
    )
    if nominal_player_ids is not None:
        scope_note = (
            "Escalação nominal do trecho; ausências temporárias não alteram a lista.\n"
            "Jogadores sem passes aparecem na tabela, sem posição no campo."
        )
    elif window_label is None:
        scope_note = "Jogadores de momentos diferentes aparecem juntos."
    else:
        scope_note = "A tabela inclui apenas jogadores com origens de passe no recorte."

    figure.text(
        0.5,
        0.025,
        "Setas indicam A → B entre posições médias; "
        "não são trajetórias individuais.\n"
        f"{scope_note} As tabelas informam o tamanho das amostras.\n"
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
